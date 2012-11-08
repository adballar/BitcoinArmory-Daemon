################################################################################
#
# Copyright (C) 2012, Ian Coleman
# Distributed under the GNU Affero General Public License (AGPL v3)
# See http://www.gnu.org/licenses/agpl.html
#
################################################################################

# This is a json-rpc interface to armory - http://bitcoinarmory.com/
#
# Where possible this follows conventions established by the Satoshi client.
# Does not require armory to be installed or running, this is a standalone application.
# Requires bitcoind process to be running before starting armory-daemon.
# Requires an armory watch-only wallet to be in the same folder as the
# armory-daemon script.
# Works with testnet, use --testnet flag when starting the script.
#
# BEWARE:
# This is relatively untested, please use caution. There should be no chance for
# irreversible damage to be done by this software, but it is still in the early
# development stage so treat it with the appropriate level of skepticism.

# Many thanks must go to etotheipi who started the armory client, and who has
# provided immense amounts of help with this. This app is mostly chunks
# of code taken from armory and refurbished into an rpc client.
# See the bitcontalk thread for more details about this software:
# https://bitcointalk.org/index.php?topic=92496.0

from twisted.web import server
from twisted.internet import reactor
from txjsonrpc.web import jsonrpc

import datetime
import decimal
import os
import sys

RPC_PORT = 7070
STANDARD_FEE = 0.0005 # BTC
        
class Wallet_Json_Rpc_Server(jsonrpc.JSONRPC):

    def __init__(self, wallet):
        self.wallet = wallet

    def jsonrpc_getnewaddress(self):
        addr = self.wallet.getNextUnusedAddress()
        return addr.getAddrStr()

    def jsonrpc_getbalance(self):
        int_balance = self.wallet.getBalance()
        decimal_balance = decimal.Decimal(int_balance) / decimal.Decimal(ONE_BTC)
        return float(decimal_balance)

    def jsonrpc_getreceivedbyaddress(self, address):
        if CLI_OPTIONS.offline:
            raise ValueError('Cannot get received amount when offline')
        # Only gets correct amount for addresses in the wallet, otherwise 0
        addr160 = addrStr_to_hash160(address)
        txs = self.wallet.getAddrTxLedger(addr160)
        balance = sum([x.getValue() for x in txs if x.getValue() > 0])
        decimal_balance = decimal.Decimal(balance) / decimal.Decimal(ONE_BTC)
        float_balance = float(decimal_balance)
        return float_balance

    def jsonrpc_sendtoaddress(self, bitcoinaddress, amount):
        if CLI_OPTIONS.offline:
            raise ValueError('Cannot create transactions when offline')
        return self.create_unsigned_transaction(bitcoinaddress, amount)

    def jsonrpc_listtransactions(self, tx_count=10, from_tx=0):
        #TODO this needs more work
        # - populate the rest of the values in tx_info
        #      - fee
        #      - blocktime
        #      - timereceived
        # Thanks to unclescrooge for inclusions - https://bitcointalk.org/index.php?topic=92496.msg1282975#msg1282975
        # NOTE that this does not use 'account' like in the Satoshi client
        final_tx_list = []
        txs = self.wallet.getTxLedger('blk')
        for tx in txs[from_tx:]:
            account = ''
            txHashBin = tx.getTxHash()#hex_to_binary(tx.getTxHash())
            cppTx = TheBDM.getTxByHash(txHashBin)
            pytx = PyTx().unserialize(cppTx.serialize())
            for txout in pytx.outputs:
                scrType = getTxOutScriptType(txout.binScript)
                if not scrType in (TXOUT_SCRIPT_STANDARD, TXOUT_SCRIPT_COINBASE):
                  continue
                address = hash160_to_addrStr(TxOutScriptExtractAddr160(txout.binScript))
                if self.wallet.hasAddr(address) == False:
                  continue
                else:
                    break
            if tx.getValue() < 0:
                category = 'send'
            else:
                category = 'receive'
            amount = float(decimal.Decimal(tx.getValue()) / decimal.Decimal(ONE_BTC))
            confirmations = TheBDM.getTopBlockHeader().getBlockHeight() - tx.getBlockNum()+1
            blockhash = 'TODO'
            blockindex = 'TODO'#tx.getBlockNum()
            txid = str(binary_to_hex(tx.getTxHash()))
            time = 'TODO'#tx.getTxTime()
            tx_info = {
                'account':account,
                'address':address,
                'category':category,
                'amount':amount,
                #'fee': -0,
                'confirmations':confirmations,
                'blockhash':blockhash,
                'blockindex':blockindex,
                #'blocktime': blocktime,
                'txid':txid,
                'time:':time,
                #'timereceived': timereceived
                }
            final_tx_list.append(tx_info)
            if len(final_tx_list) >= tx_count:
                break
        return final_tx_list

        
    # https://bitcointalk.org/index.php?topic=92496.msg1126310#msg1126310
    def create_unsigned_transaction(self, bitcoinaddress_str, amount_to_send_btc):
        # Get unspent TxOutList and select the coins
        addr160_recipient = addrStr_to_hash160(bitcoinaddress_str)
        totalSend, fee = long(amount_to_send_btc * ONE_BTC), (STANDARD_FEE * ONE_BTC)
        spendBal = self.wallet.getBalance('Spendable')
        utxoList = self.wallet.getTxOutList('Spendable')
        utxoSelect = PySelectCoins(utxoList, totalSend, fee)

        minFeeRec = calcMinSuggestedFees(utxoSelect, totalSend, fee)[1]
        if fee<minFeeRec:
            if totalSend + minFeeRec > spendBal:
                raise NotEnoughCoinsError, "You can't afford the fee!"
            utxoSelect = PySelectCoins(utxoList, totalSend, minFeeRec)
            fee = minFeeRec

        if len(utxoSelect)==0:
            raise CoinSelectError, "Somehow, coin selection failed.  This shouldn't happen"

        totalSelected = sum([u.getValue() for u in utxoSelect])
        totalChange = totalSelected - (totalSend  + fee)

        outputPairs = []
        outputPairs.append( [addr160_recipient, totalSend] )
        if totalChange > 0:
            outputPairs.append( [self.wallet.getNextUnusedAddress().getAddr160(), totalChange] )

        random.shuffle(outputPairs)
        txdp = PyTxDistProposal().createFromTxOutSelection(utxoSelect, outputPairs)

        return txdp.serializeAscii()

class Armory_Daemon():

    def __init__(self):

        sys.stdout.write("\nReading wallet file")
        self.wallet = self.find_wallet()

        use_blockchain = not CLI_OPTIONS.offline
        if(use_blockchain):
            sys.stdout.write("\nLoading blockchain")
            self.loadBlockchain()

        sys.stdout.write("\nInitialising server")
        reactor.listenTCP(RPC_PORT, server.Site(Wallet_Json_Rpc_Server(self.wallet)))

        if(use_blockchain):
          self.NetworkingFactory = ArmoryClientFactory( \
                                  func_loseConnect=self.showOfflineMsg, \
                                  func_madeConnect=self.showOnlineMsg, \
                                  func_newTx=self.newTxFunc)
          reactor.connectTCP('127.0.0.1', BITCOIN_PORT, self.NetworkingFactory)
          reactor.callLater(5, self.Heartbeat)
        self.start()

    def start(self):
        sys.stdout.write("\nServer started")
        reactor.run()

    def newTxFunc(self, pytxObj):
        # Cut down version from ArmoryQt.py
        TheBDM.addNewZeroConfTx(pytxObj.serialize(), long(RightNow()), True)
        TheBDM.rescanWalletZeroConf(self.wallet.cppWallet)

        # TODO set up a 'subscribe' feature so these notifications can be
        # pushed out to interested parties.

        # From here down is display purposes only, copied from ArmoryQt.py
        message = "New TX"
        le = self.wallet.cppWallet.calcLedgerEntryForTxStr(pytxObj.serialize())
        if not le.isSentToSelf():
            txref = TheBDM.getTxByHash(le.getTxHash())
            nOut = txref.getNumTxOut()
            recips = [txref.getTxOut(i).getRecipientAddr() for i in range(nOut)]
            values = [txref.getTxOut(i).getValue()         for i in range(nOut)]
            idxMine  = filter(lambda i:     self.wallet.hasAddr(recips[i]), range(nOut))
            idxOther = filter(lambda i: not self.wallet.hasAddr(recips[i]), range(nOut))
            mine  = [(recips[i],values[i]) for i in idxMine]
            other = [(recips[i],values[i]) for i in idxOther]

            # Collected everything we need to display, now construct it and do it
            if le.getValue()>0:
               # Received!
               message = 'Bitcoins Received!'
               totalStr = coin2str( sum([mine[i][1] for i in range(len(mine))]), maxZeros=1)
               message += '\nAmount: \t%s BTC' % totalStr.strip()
               if len(mine)==1:
                  message += '\nAddress:\t%s' % hash160_to_addrStr(mine[0][0])
                  addrComment = self.wallet.getComment(mine[0][0])
                  #if addrComment:
                     #message += '\n%s...' % addrComment[:24]
               else:
                  message += '\n<Received with Multiple Addresses>'
            elif le.getValue()<0:
               # Sent!
               message = 'Bitcoins Sent!'
               totalStr = coin2str( sum([other[i][1] for i in range(len(other))]), maxZeros=1)
               message += '\nAmount: \t%s BTC' % totalStr.strip()
               if len(other)==1:
                  message += 'Sent To:\t%s' % hash160_to_addrStr(other[0][0])
                  addrComment = self.wallet.getComment(other[0][0])
                  #if addrComment:
                     #message += '\n%s...' % addrComment[:24]
               else:
                  dispLines.append('<Sent to Multiple Addresses>')
        else:
            amt = self.determineSentToSelfAmt(le, self.wallet)[0]
            message = 'Wallet "%s" just sent %s BTC to itself!' % \
               (self.wallet.labelName, coin2str(amt,maxZeros=1).strip())
               
        sys.stdout.write("\n" + message)

    def determineSentToSelfAmt(self, le, wlt):
      """
      NOTE:  this method works ONLY because we always generate a new address
             whenever creating a change-output, which means it must have a
             higher chainIndex than all other addresses.  If you did something
             creative with this tx, this may not actually work.
      """
      amt = 0
      if TheBDM.isInitialized() and le.isSentToSelf():
         txref = TheBDM.getTxByHash(le.getTxHash())
         if not txref.isInitialized():
            return (0, 0)
         if txref.getNumTxOut()==1:
            return (txref.getTxOut(0).getValue(), -1)
         maxChainIndex = -5
         txOutChangeVal = 0
         txOutIndex = -1
         valSum = 0
         for i in range(txref.getNumTxOut()):
            valSum += txref.getTxOut(i).getValue()
            addr160 = txref.getTxOut(i).getRecipientAddr()
            addr    = wlt.getAddrByHash160(addr160)
            if addr and addr.chainIndex > maxChainIndex:
               maxChainIndex = addr.chainIndex
               txOutChangeVal = txref.getTxOut(i).getValue()
               txOutIndex = i

         amt = valSum - txOutChangeVal
      return (amt, txOutIndex)

    def showOfflineMsg(self):
        sys.stdout.write("\n%s Offline - not tracking blockchain" % datetime.now().isoformat())

    def showOnlineMsg(self):
        sys.stdout.write("\n%s Online - tracking blockchain" % datetime.now().isoformat())

    def find_wallet(self):
        fnames = os.listdir(os.getcwd())
        for fname in fnames:
            is_wallet = fname[-7:] == ".wallet"
            is_watchonly = fname.find("watchonly") > -1
            is_backup = fname.find("backup") > -1
            if(is_wallet and is_watchonly and not is_backup):
                wallet = PyBtcWallet().readWalletFile(fname)
                sys.stdout.write("\nUsing wallet file %s" % fname)
                return wallet
        raise ValueError('Unable to locate a watch-only wallet in %s' % os.getcwd())

    def loadBlockchain(self):
        BDM_LoadBlockchainFile()
        # Thanks to unclescrooge for inclusions - https://bitcointalk.org/index.php?topic=92496.msg1282975#msg1282975
        self.latestBlockNum = TheBDM.getTopBlockHeader().getBlockHeight()

        # Now that theb blockchain is loaded, let's populate the wallet info
        if TheBDM.isInitialized():
            mempoolfile = os.path.join(ARMORY_HOME_DIR,'mempool.bin')
            self.checkMemoryPoolCorruption(mempoolfile)
            TheBDM.enableZeroConf(mempoolfile)

            # self.statusBar().showMessage('Syncing wallets with blockchain...')
            sys.stdout.write("\nSyncing wallets with blockchain")
            sys.stdout.write("\nSyncing wallet: %s" % self.wallet.uniqueIDB58)
            self.wallet.setBlockchainSyncFlag(BLOCKCHAIN_READONLY)
            self.wallet.syncWithBlockchain()

    def checkMemoryPoolCorruption(self, mempoolname):
        if not os.path.exists(mempoolname):
            return

        memfile = open(mempoolname, 'r')
        memdata = memfile.read()
        memfile.close()

        binunpacker = BinaryUnpacker(memdata)
        try:
            while binunpacker.getRemainingSize() > 0:
                binunpacker.get(UINT64)
                PyTx().unserialize(binunpacker)
        except:
            os.remove(mempoolname);
            #LOGWARN('Memory pool file was corrupt.  Deleted. (no further action is needed)')

    def Heartbeat(self, nextBeatSec=2):
        """
        This method is invoked when the app is initialized, and will
        run every 2 seconds, or whatever is specified in the nextBeatSec
        argument.
        """
        # Check for new blocks in the blk000X.dat file
        if TheBDM.isInitialized():
            sys.stdout.write(".")
            sys.stdout.flush()
            newBlks = TheBDM.readBlkFileUpdate()
            self.topTimestamp   = TheBDM.getTopBlockHeader().getTimestamp()
            if newBlks>0:
                self.latestBlockNum = TheBDM.getTopBlockHeader().getBlockHeight()
                didAffectUs = False
                prevLedgerSize = len(self.wallet.getTxLedger())
                self.wallet.syncWithBlockchain()
                TheBDM.rescanWalletZeroConf(self.wallet.cppWallet)

        self.wallet.checkWalletLockTimeout()

        reactor.callLater(nextBeatSec, self.Heartbeat)

                
if __name__ == "__main__":
    from armoryengine import *
    rpc_server = Armory_Daemon()

import os
import threading
from time import sleep
from enum import Enum
from twisted.internet import reactor, task
from shutil import copyfile
from neo.Core.Blockchain import Blockchain
from neo.Network.NodeLeader import NodeLeader
from neo.Implementations.Blockchains.LevelDB.LevelDBBlockchain import LevelDBBlockchain
from neo.Settings import settings
from neo.Implementations.Wallets.peewee.UserWallet import UserWallet
from prompt_toolkit import prompt
from neo.Wallets.utils import to_aes_key
from logzero import setup_logger
from base58 import b58encode_check

from neocore.Fixed8 import Fixed8

from neo.Prompt.Commands.Invoke import InvokeContract, TestInvokeContract


class NetworkType(Enum):
    PRIVNET = 1
    TESTNET = 2
    MAINNET = 3


class BlockchainMain:
    logger = None
    network_type = None
    blockchain = None

    wallet_path = None
    wallet = None
    wallet_passwd_key = None

    def __init__(self, network_type, logger_name):
        self.network_type = network_type
        # If you want the log messages to also be saved in a logfile, enable the
        # next line. This configures a logfile with max 10 MB and 3 rotations:
        # settings.set_logfile("/tmp/logfile.log", max_bytes=1e7, backup_count=3)
        self.logger = setup_logger(logger_name)

    def run(self):

        # bl: changing to 8 as recommended in the 8-10 range by localhuman (previously had this at 150)
        settings.set_max_peers(8)

        # Setup the blockchain
        self.blockchain = LevelDBBlockchain(settings.chain_leveldb_path)
        Blockchain.RegisterBlockchain(self.blockchain)
        NodeLeader.Instance().Start()
        dbloop = task.LoopingCall(Blockchain.Default().PersistBlocks)
        dbloop.start(.1)
        Blockchain.Default().PersistBlocks()

        # Disable smart contract events for external smart contracts
        settings.set_log_smart_contract_events(False)

        # if the wallet was set up (by setting a path and loading the password), then open it!
        if self.wallet_path:
            self.wallet_open()

        # Start a thread with custom code
        d = threading.Thread(target=self.custom_background_code)
        d.setDaemon(True)  # daemonizing the thread will kill it when the main thread is quit
        d.start()

        # invoke any pre-start action that needs to occur before we start the reactor.
        # optional for subclasses to implement.
        self.pre_start()

        # Run all the things (blocking call)
        self.logger.info("Everything setup and running. Waiting for events...")
        reactor.run()
        self.logger.info("Shutting down")
        if self.wallet_path:
            self.wallet_close()
        Blockchain.Default().Dispose()
        NodeLeader.Instance().Shutdown()
        self.logger.info("Shut down.")

    def pre_start(self):
        pass

    def custom_background_code(self):
        """ Custom code run in a background thread. Prints the current block height.

        This function is run in a daemonized thread, which means it can be instantly killed at any
        moment, whenever the main thread quits. If you need more safety, don't use a  daemonized
        thread and handle exiting this thread in another way (eg. with signals and events).
        """
        while True:
            self.logger.info("Block %s / %s", str(Blockchain.Default().Height), str(Blockchain.Default().HeaderHeight))
            sleep(60)

    @staticmethod
    def get_address(raw_address):
        # bl: NEO uses the AddressVersion 23, so stick that on the front of the address before doing the b58check
        raw_address = b'\x17' + raw_address
        return b58encode_check(raw_address).decode('utf-8')

    def setup_wallet(self, wallet_path):
        if not os.path.exists(wallet_path):
            raise ValueError("Wallet file not found")

        self.wallet_path = wallet_path

        wallet_passwd = prompt("[password]> ", is_password=True)
        self.wallet_passwd_key = to_aes_key(wallet_passwd)

        self.setup_network()

    def setup_network(self):
        if self.network_type == NetworkType.TESTNET:
            settings.setup_testnet()
        elif self.network_type == NetworkType.MAINNET:
            settings.setup_mainnet()
        elif self.network_type == NetworkType.PRIVNET:
            settings.setup_privnet()
        else:
            raise ValueError('Unknown wallet %s' % self.wallet_path)

    def wallet_open(self):
        self.wallet = UserWallet.Open(self.wallet_path, self.wallet_passwd_key)
        # bl: there is some side effect happening here that allows the wallet to be fully/properly initialized.
        # without this, there are errors when sending NEP5 tokens (same goes in prompt.py).
        # don't have time to investigate right now, so doing this as a hack to get things working properly
        self.wallet.ToJson()

        # _walletdb_loop = task.LoopingCall(wallet.ProcessBlocks)
        # _walletdb_loop.start(1)
        self.logger.info("Opened wallet at %s", self.wallet_path)

    def wallet_sync(self):
        self.wallet.ProcessBlocks(0)

    def wallet_close(self):
        # _walletdb_loop.stop()
        self.wallet_sync()
        self.wallet.Close()

    def recover_wallet(self):
        self.logger.warn("recovering wallet...")
        syncd_file_path = self.wallet_path + ".syncd"
        # check if the syncd wallet exists, and raise an exception if it does not!
        if not os.path.exists(syncd_file_path):
            raise EnvironmentError("Could not find file %s" % syncd_file_path)
        self.wallet_close()
        os.remove(self.wallet_path)
        copyfile(syncd_file_path, self.wallet_path)
        self.wallet_open()
        self.wallet_sync()
        self.logger.warn("wallet recovered!")

    def wait_for_peers(self):
        while len(NodeLeader.Instance().Peers) == 0:
            self.logger.debug('waiting for NodeLeader peers')
            sleep(1)

    def test_invoke(self, args, expected_result_count, test_only=False, from_addr=None):
        if args and len(args) > 0:
            tx, fee, results, num_ops = TestInvokeContract(self.wallet, args, from_addr=from_addr)

            if tx is not None and results is not None:
                print(
                    "\n-------------------------------------------------------------------------------------------------------------------------------------")
                print("Test invoke successful")
                print("Total operations: %s" % num_ops)
                print("Results %s" % [str(item) for item in results])
                print("Invoke TX GAS cost: %s" % (tx.Gas.value / Fixed8.D))
                print("Invoke TX fee: %s" % (fee.value / Fixed8.D))
                print(
                    "-------------------------------------------------------------------------------------------------------------------------------------\n")

                if results[0].GetBigInteger() != expected_result_count:
                    self.logger.error("Found invalid result! '%s' but expected '%s'" % (results[0], expected_result_count))

                if test_only:
                    return True

                # bl: tx can fail if there are no connected peers, so wait for one
                self.wait_for_peers()

                return InvokeContract(self.wallet, tx, fee, from_addr)
            else:
                print("Error testing contract invoke: %s" % args)
        else:
            print("Invalid args for test_invoke! %s" % args)

        return False

    def shutdown(self):
        self.logger.info("Shutdown invoked")
        reactor.stop()

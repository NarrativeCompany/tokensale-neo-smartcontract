import asyncio
import os
import getpass
from contextlib import suppress
from enum import Enum
from shutil import copyfile
from signal import SIGINT, SIGHUP, SIGTERM

from base58 import b58encode_check
from logzero import setup_logger

import neo.Storage.Implementation.DBFactory as DBFactory
from neo.Core.Blockchain import Blockchain
from neo.Core.Fixed8 import Fixed8
from neo.Implementations.Notifications.NotificationDB import NotificationDB
from neo.Implementations.Wallets.peewee.UserWallet import UserWallet
from neo.Network.nodemanager import NodeManager
from neo.Network.p2pservice import NetworkService
from neo.Prompt.Commands.Invoke import InvokeContract, TestInvokeContract
from neo.Settings import settings
from neo.Wallets.utils import to_aes_key
from neo.logging import log_manager

logger = log_manager.getLogger()


class NetworkType(Enum):
    PRIVNET = 1
    TESTNET = 2
    MAINNET = 3


class BlockchainMain:
    logger = None
    network_type = None
    blockchain = None

    wallet_path = None
    syncd_wallet_path = None
    wallet = None
    wallet_passwd_key = None

    def __init__(self, network_type, logger_name):
        self.network_type = network_type
        # If you want the log messages to also be saved in a logfile, enable the
        # next line. This configures a logfile with max 10 MB and 3 rotations:
        # settings.set_logfile("/tmp/logfile.log", max_bytes=1e7, backup_count=3)
        self.logger = setup_logger(logger_name)

    def pre_start(self):
        pass

    async def run_loop(self):
        nodemgr = NodeManager()
        while not nodemgr.running:
            await asyncio.sleep(0.1)

        await self.custom_background_code()

    async def custom_background_code(self):
        """ Custom code run in a background thread. Prints the current block height.

        This function is run in a daemonized thread, which means it can be instantly killed at any
        moment, whenever the main thread quits. If you need more safety, don't use a  daemonized
        thread and handle exiting this thread in another way (eg. with signals and events).
        """
        while True:
            self.logger.info("Block %s / %s", str(Blockchain.Default().Height), str(Blockchain.Default().HeaderHeight))
            await asyncio.sleep(60)

    @staticmethod
    def get_address(raw_address):
        # bl: NEO uses the AddressVersion 23, so stick that on the front of the address before doing the b58check
        raw_address = b'\x17' + raw_address
        return b58encode_check(raw_address).decode('utf-8')

    def setup_wallet(self, wallet_path):
        if not os.path.exists(wallet_path):
            raise ValueError("Wallet file not found")

        self.wallet_path = wallet_path
        self.syncd_wallet_path = wallet_path + ".syncd"

        if not os.path.exists(self.syncd_wallet_path):
            self.logger.info("Creating syncd copy of wallet file...")
            copyfile(self.wallet_path, self.syncd_wallet_path)

        wallet_passwd = getpass.getpass()
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

    async def wallet_sync(self, start_block=None, rebuild=False):
        await self.wallet.sync_wallet(start_block, rebuild)
        while not self.wallet.IsSynced:
            self.logger.debug('waiting for wallet to be synced.')
            await asyncio.sleep(1)
        self.logger.debug('wallet synced!')

    async def wallet_close(self):
        # _walletdb_loop.stop()
        await self.wallet_sync()
        self.wallet.Close()

    async def recover_wallet(self):
        self.logger.warn("recovering wallet...")
        # check if the syncd wallet exists, and raise an exception if it does not!
        if not os.path.exists(self.syncd_wallet_path):
            raise EnvironmentError("Could not find file %s" % self.syncd_wallet_path)
        await self.wallet_close()
        os.remove(self.wallet_path)
        copyfile(self.syncd_wallet_path, self.wallet_path)
        self.wallet_open()
        await self.wallet_sync()
        self.logger.warn("wallet recovered!")

    async def rebuild_wallet(self, start_block):
        self.logger.warn("rebuilding wallet from block %s..." % start_block)
        await self.wallet_sync(start_block, True)
        self.logger.warn("wallet rebuilt!")

    async def wait_for_peers(self):
        while len(NodeManager().nodes) < 10:
            self.logger.debug('waiting for at least 10 NodeManager peers. currently %s connected.' % len(NodeManager().nodes))
            await asyncio.sleep(1)
        self.logger.debug('%s connected NodeManager peers!' % len(NodeManager().nodes))

    async def test_invoke(self, args, expected_result_count, test_only=False, from_addr=None):
        if args and len(args) > 0:
            tx, fee, results, num_ops, success = TestInvokeContract(self.wallet, args, from_addr=from_addr)

            if tx is not None and results is not None and success:
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
                await self.wait_for_peers()

                # bl: may have waited a while for peers, so make sure the wallet is syncd before invoking the transaction
                await self.wallet_sync()

                return InvokeContract(self.wallet, tx, fee, from_addr)
            else:
                print("Error testing contract invoke: %s" % args)
        else:
            print("Invalid args for test_invoke! %s" % args)

        return False

    @staticmethod
    def quit():
        print('Shutting down. This may take a bit...')
        raise SystemExit

    def run(self):
        # bl: changing to 15 so that we can get connections with a high number to improve transaction relayability
        settings.set_max_peers(15)

        loop = asyncio.get_event_loop()

        # because a KeyboardInterrupt is so violent it can shutdown the DB in an unpredictable state.
        loop.add_signal_handler(SIGINT, quit)
        loop.add_signal_handler(SIGHUP, quit)
        loop.add_signal_handler(SIGTERM, quit)

        # Disable smart contract events for external smart contracts
        settings.set_log_smart_contract_events(False)

        # Instantiate the blockchain and subscribe to notifications
        blockchain = Blockchain(DBFactory.getBlockchainDB(settings.chain_leveldb_path))
        Blockchain.RegisterBlockchain(blockchain)

        # Try to set up a notification db
        if NotificationDB.instance():
            NotificationDB.instance().start()

        # if the wallet was set up (by setting a path and loading the password), then open it!
        if self.wallet_path:
            self.wallet_open()

        # invoke any pre-start action that needs to occur before we start the loop.
        # optional for subclasses to implement.
        self.pre_start()

        blockchain_main_task = loop.create_task(self.run_loop())
        p2p = NetworkService()
        loop.create_task(p2p.start())

        async def shutdown():
            all_tasks = asyncio.all_tasks()
            for task in all_tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

        try:
            loop.run_forever()
        except (SystemExit,KeyboardInterrupt):
            with suppress((SystemExit, Exception)):
                blockchain_main_task.exception()
            loop.run_until_complete(p2p.shutdown())
            loop.run_until_complete(shutdown())
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.stop()
        finally:
            loop.close()

        # Run things

        if self.wallet_path:
            logger.info("Closing wallet file %s" % self.wallet_path)
            asyncio.run(self.wallet_close())

        # After the reactor is stopped, gracefully shutdown the database.
        logger.info("Closing databases...")
        NotificationDB.close()
        Blockchain.Default().Dispose()

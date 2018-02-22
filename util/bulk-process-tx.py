"""
Based off of: https://github.com/CityOfZion/neo-python/blob/master/examples/smart-contract.py

Example of running a NEO node and receiving notifications when events
of a specific smart contract happen.

Events include Runtime.Notify, Runtime.Log, Storage.*, Execution.Success
and several more. See the documentation here:

http://neo-python.readthedocs.io/en/latest/smartcontracts.html

Usage:

* Place this file in neo-python/neo/contrib and execute the following from neo-python dir:

python3.5 -m venv venv
source venv/bin/activate
# bl: these only need to be done once
#pip install -U setuptools pip wheel
#pip install -e .
python neo/contrib/bulk-process-tx.py

"""
import os
import threading
from time import sleep

from logzero import setup_logger
from twisted.internet import reactor, task

from neo.contrib.smartcontract import SmartContract
from neo.Network.NodeLeader import NodeLeader
from neo.Core.Blockchain import Blockchain
from neo.Implementations.Blockchains.LevelDB.LevelDBBlockchain import LevelDBBlockchain
from neo.Settings import settings

from neo.Implementations.Wallets.peewee.UserWallet import UserWallet

from neo.Prompt.Utils import parse_param

from neocore.Fixed8 import Fixed8

from neo.Prompt.Commands.Invoke import InvokeContract, TestInvokeContract

from prompt_toolkit import prompt

from pyparsing import ZeroOrMore, Regex

from shutil import copyfile

logger = setup_logger('bulk-process-tx')

# from InputParser
parser = ZeroOrMore(Regex(r'\[[^]]*\]') | Regex(r'"[^"]*"') | Regex(r'\'[^\']*\'') | Regex(r'[^ ]+'))

# STEP 1: set the smart contract hash
SMART_CONTRACT_HASH = "a721d5893480260bd28ca1f395f2c465d0b5b1c2"

# STEP 2: set:
# * the operation to run
# * the expected number of args in the params array (to prevent against string parsing issues that could break the invocation)
# * the expected result count for validation

# operation = "transfer_presale_tokens"
# operation_args_array_length = 2
# expected_result_count = 1

operation = "crowdsale_register"
operation_args_array_length = 6
expected_result_count = 6

# STEP 3: set test_only = True initially to make sure the testinvokes look good.

test_only = False

wallet = None
testnet_wallet_path = "testnet.wallet"
mainnet_wallet_path = "mainnet.wallet"
privnet_wallet_path = "privnet.wallet"

# STEP 4: set the wallet_path (will be used to determine which network to connect to)

wallet_path = mainnet_wallet_path
wallet_passwd = None
wallet_needs_recovery = False

# If you want the log messages to also be saved in a logfile, enable the
# next line. This configures a logfile with max 10 MB and 3 rotations:
# settings.set_logfile("/tmp/logfile.log", max_bytes=1e7, backup_count=3)

# Setup the smart contract instance
smart_contract = SmartContract(SMART_CONTRACT_HASH)

blockchain = None

job = None

# STEP 5: set the array of job arguments to process

jobs = [
    '["AL6rzfw8nnFxMENhgtHvAiQpPwqsAWmRsp","ALrPRjmpLjRZZiZnwE3YbBA4jDgnSDMyKf","ARKprNnuZv8fuLXhpypGEdPHXWKb2rokcE","AKEYuC5oc1xBp7cs7GeF9hqPPSe8yTQGeG","ALKm4fVt1JXPdgb6YHRDbkgvhNCWuqLdY6","Abi7oAJiXWDwfpquKTTJCn95WyuyRtWk1A"]',
    '["AWLuCogCStMqCGpYptY7sTsW6Y6triK8Bf","AHG39uqovauMASpw623kZhGUTyvZXxb2bv","AKnYLgwpvx5M3dYiNqsEkrWHn3UHuk3YPg","AQYmEHTQ7hVXnw3q8Qca9Q9yN2gQGgk33e","ATe9PXtBT9mpD4JxUnBk4JPix9yfnprBQK","ATtRKaQLM26zEpMTqUgi1DbUuPP7CtkdLo"]'
]

jobs_processed = 0
tx_processing = None

# Register an event handler for Runtime.Notify events of the smart contract.
@smart_contract.on_notify
def sc_notify(event):
    if not event.execution_success:
        return

    prefix = ""
    if event.test_mode:
        prefix = "[test_mode]"
    elif event.tx_hash != tx_processing:
        # only emit notify events for the transaction that we are waiting on
        return

    logger.info(prefix + "[SmartContract.Runtime.Notify] [%s] [tx %s] %s", event.contract_hash, event.tx_hash, event.event_payload)


# Register an event handler for Runtime.Notify events of the smart contract.
@smart_contract.on_storage
def sc_storage(event):
    prefix = ""
    if event.test_mode:
        prefix = "[test_mode]"
    elif event.tx_hash != tx_processing:
        # only emit notify events for the transaction that we are waiting on
        return

    logger.info(prefix + "[%s] [%s] [tx %s] %s", event.event_type, event.contract_hash, event.tx_hash, event.event_payload)


@smart_contract.on_execution
def sc_execution(event):
    # only emit execution events for the transaction that we are waiting on
    if event.tx_hash != tx_processing:
        return

    if not event.execution_success:
        logger.error("[execution_success=false][SmartContract.Runtime.Notify] [%s] [tx %s] %s", event.contract_hash, event.tx_hash, event.event_payload)
        return

    prefix = ""
    if event.test_mode:
        prefix = "[test_mode]"

    logger.info(prefix + "[SmartContract.Execution.Success] [%s] [tx %s] %s", event.contract_hash, event.tx_hash, event.event_payload)

    if not event.test_mode:
        global jobs_processed
        jobs_processed += 1
        process_job()


def process_job():
    global job, jobs, tx_processing

    jobs_remaining = len(jobs)

    logger.debug("%s jobs processed. %s jobs remaining.", jobs_processed, jobs_remaining)

    if jobs_remaining > 0:
        # just pop a job off the array to process next
        job = jobs[0]
        jobs = jobs[1:]
        tx_processing = None
    else:
        # change the jobs array to None (from an empty array) to indicate we are done and can shut down
        jobs = None


def custom_background_code():
    """ Custom code run in a background thread. Prints the current block height.

    This function is run in a daemonized thread, which means it can be instantly killed at any
    moment, whenever the main thread quits. If you need more safety, don't use a  daemonized
    thread and handle exiting this thread in another way (eg. with signals and events).
    """
    global job, wallet_needs_recovery, tx_processing
    while True:
        sleep(1)

        if not job:
            # no more jobs? then shut 'er down!
            if jobs is None:
                shutdown()
            continue

        if wallet_needs_recovery:
            recover_wallet()
            wallet_needs_recovery = False
        else:
            wallet.ProcessBlocks(0)

        job_args = parser.parseString(operation + " " + str(job))
        job_args = job_args[0:]

        if len(job_args) != 2:
            logger.error('ERROR! must have only 2 args (operation, params). skipping! %s', job_args)
            job = None
            process_job()
            continue

        operation_params = parse_param(job_args[1])
        if len(operation_params) != operation_args_array_length:
            logger.error('ERROR! must have exactly %d operation args, not %d. skipping! %s', operation_args_array_length, len(operation_params), job_args)
            job = None
            process_job()
            continue

        args = [SMART_CONTRACT_HASH] + job_args
        logger.debug('processing job: %s', args)
        result = test_invoke(args)

        if not result:
            # transaction failed? wallet probably out-of-sync (insufficient funds) so reload it
            wallet_needs_recovery = True
        else:
            # this job has been invoked, so clear it out. on to the next.
            job = None
            if test_only:
                # when testing but not relaying transactions, we just continue to the next job
                global jobs_processed
                jobs_processed += 1
                process_job()
            else:
                # transaction successfully relayed? then let's set the tx Hash that we're waiting for
                tx_processing = result.Hash



def test_invoke(args):
    if args and len(args) > 0:
        tx, fee, results, num_ops = TestInvokeContract(wallet, args)

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

            if str(results[0]) != ("Integer: %d " % expected_result_count):
                logger.error("Found invalid result! ''%s''", results[0])

            if test_only:
                return True

            # bl: tx can fail if there are no connected peers, so wait for one
            wait_for_peers()

            return InvokeContract(wallet, tx, fee)
        else:
            print("Error testing contract invoke: %s" % args)
    else:
        print("Invalid args for test_invoke! %s" % args)

    return False


def wait_for_peers():
    while len(NodeLeader.Instance().Peers) == 0:
        logger.debug('waiting for NodeLeader peers')
        sleep(1)


def wallet_open():
    global wallet
    wallet = UserWallet.Open(wallet_path, wallet_passwd)

    # _walletdb_loop = task.LoopingCall(wallet.ProcessBlocks)
    # _walletdb_loop.start(1)
    logger.info("Opened wallet at %s", wallet_path)


def wallet_close():
    # _walletdb_loop.stop()
    wallet.Close()


def recover_wallet():
    logger.warn("recovering wallet...")
    wallet_close()
    os.remove(wallet_path)
    copyfile(wallet_path + ".syncd", wallet_path)
    wallet_open()
    wallet.ProcessBlocks(0)
    logger.warn("wallet recovered!")


def main():
    # Setup the blockchain
    global blockchain, wallet_passwd

    if not os.path.exists(wallet_path):
        logger.error("Wallet file not found")
        return

    wallet_passwd = prompt("[password]> ", is_password=True)

    if wallet_path == testnet_wallet_path:
        settings.setup_testnet()
    elif wallet_path == mainnet_wallet_path:
        settings.setup_mainnet()
    elif wallet_path == privnet_wallet_path:
        settings.setup_privnet()
    else:
        raise ValueError('Unknown wallet %s' % wallet_path)
    blockchain = LevelDBBlockchain(settings.LEVELDB_PATH)
    Blockchain.RegisterBlockchain(blockchain)
    NodeLeader.Instance().Start()
    dbloop = task.LoopingCall(Blockchain.Default().PersistBlocks)
    dbloop.start(.1)
    Blockchain.Default().PersistBlocks()

    # Disable smart contract events for external smart contracts
    settings.set_log_smart_contract_events(False)

    wallet_open()

    # Start a thread with custom code
    d = threading.Thread(target=custom_background_code)
    d.setDaemon(True)  # daemonizing the thread will kill it when the main thread is quit
    d.start()

    # trigger the first job to be processed
    process_job()

    # Run all the things (blocking call)
    logger.info("Everything setup and running. Waiting for events...")
    reactor.run()
    logger.info("Shutting down")
    wallet_close()
    Blockchain.Default().Dispose()
    NodeLeader.Instance().Shutdown()
    logger.info("Shut down.")


def shutdown():
    logger.info("Shutdown invoked")
    reactor.stop()


if __name__ == "__main__":
    main()

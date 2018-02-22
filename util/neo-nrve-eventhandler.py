"""
Based off of: https://github.com/CityOfZion/neo-python/blob/master/examples/smart-contract.py

Example of running a NEO node and receiving notifications when events
of a specific smart contract happen.

Events include Runtime.Notify, Runtime.Log, Storage.*, Execution.Success
and several more. See the documentation here:

http://neo-python.readthedocs.io/en/latest/smartcontracts.html

Usage:

* Update smart_contract script hash
* Update pymysql.connect statement details (database host, user, password, db)
* Update SMTP details (host, port, TLS, etc.), if necessary
* Place this file in neo-python/neo/contrib and execute the following from neo-python dir:

python3.5 -m venv venv
source venv/bin/activate
# bl: these only need to be done once
#pip install -U setuptools pip wheel
#pip install -e .
python neo/contrib/neo-nrve-eventhandler.py

"""
import os
import threading
from time import sleep
from base58 import b58encode_check

from logzero import logger
from twisted.internet import reactor, task

from neo.contrib.smartcontract import SmartContract
from neo.Network.NodeLeader import NodeLeader
from neo.Core.Blockchain import Blockchain
from neo.Implementations.Blockchains.LevelDB.LevelDBBlockchain import LevelDBBlockchain
from neo.Settings import settings

import pymysql.cursors
from pymysql import MySQLError

import smtplib
from email.mime.text import MIMEText

# If you want the log messages to also be saved in a logfile, enable the
# next line. This configures a logfile with max 10 MB and 3 rotations:
# settings.set_logfile("/tmp/logfile.log", max_bytes=1e7, backup_count=3)

# Setup the smart contract instance
smart_contract = SmartContract("08ef61ed965d5d72a735b2399373d588eaf3c495")
old_smart_contract = SmartContract("5fd4d17a37e8681f58b169aba90d0b9f06cb6d5e")

blockchain = None

# Register an event handler for Runtime.Notify events of both the old and new smart contracts.
@old_smart_contract.on_notify
@smart_contract.on_notify
def sc_notify(event):

    # Make sure that the event payload list has at least one element.
    if not len(event.event_payload):
        logger.info("[no event_payload] SmartContract Runtime.Notify event: %s", event)
        return

    if event.test_mode:
        logger.info("[test_mode] SmartContract Runtime.Notify event: %s", event)
        return

    if not event.execution_success:
        logger.info("[execution_success=false] SmartContract Runtime.Notify event: %s", event)
        return

    # The event payload list has at least one element. As developer of the smart contract
    # you should know what data-type is in the bytes, and how to decode it. In this example,
    # it's just a string, so we decode it with utf-8:
    event_type = event.event_payload[0].decode("utf-8")
    block_number = event.block_number

    timestamp = blockchain.GetHeaderByHeight(block_number).Timestamp

    # bl: event.contract_hash is a UInt160, so convert it to a hex string
    contract_hash = event.contract_hash.ToString()
    # bl: event.tx_hash is a UInt256, so convert it to a hex string
    tx_hash = event.tx_hash.ToString()

    # bl: we only care about refunds for the old smart contract
    if contract_hash == old_smart_contract.contract_hash and event_type != 'refund':
        return

    # Connect to the database
    connection = pymysql.connect(host='localhost',
                                 user='root',
                                 password='',
                                 db='global',
                                 charset='utf8mb4',
                                 cursorclass=pymysql.cursors.DictCursor)

    try:
        with connection.cursor() as cursor:
            if event_type == 'kyc_registration' or event_type == 'kyc_deregistration':
                address = get_address(event.event_payload[1])
                logger.info("- %s: %s", event_type, address)
                sql = "update `NarrativeUserNeoAddress` set whitelisted = %s where neoAddress = %s"
                args = (1 if event_type == 'kyc_registration' else 0, address)
            elif event_type == 'contribution':
                # from, neo, tokens
                address = get_address(event.event_payload[1])
                # based on the smart contract, we know these should always be whole numbers
                neo = (int)(event.event_payload[2] / 100000000)
                tokens = (int)(event.event_payload[3] / 100000000)
                logger.info("- %s: %s: %s NEO (%s NRVE) (tx: %s)", event_type, address, neo, tokens, tx_hash)
                sql = ("insert into `NarrativeContribution` (transactionId, neo, nrveTokens, transactionDate, neoAddress_oid)\n"
                       "select %s, %s, %s, from_unixtime(%s), na.oid\n"
                       "from NarrativeUserNeoAddress na\n"
                       "where na.neoAddress = %s")
                args = (tx_hash, neo, tokens, timestamp, address)
            elif event_type == 'refund':
                # to, amount
                address = get_address(event.event_payload[1])
                # based on the smart contract, the amount should always be a whole number
                amount = (int)(event.event_payload[2] / 100000000)
                log = "%s: %s: %s NEO [%s] (tx: %s)" % (event_type, address, amount, contract_hash, tx_hash)
                logger.info('- ' + log)
                sql = ("insert into `NarrativeRefund` (transactionId, contractHash, neo, transactionDate, neoAddress)\n"
                       "values (%s, %s, %s, from_unixtime(%s), %s)")
                args = (tx_hash, contract_hash, amount, timestamp, address)
                send_email("Narrative Refund Required", log, "brian@narrative.network")
            elif event_type == 'transfer' or event_type == 'approve':
                # bl: ignore NEP5 transfers and approvals. don't care about those, and there will be a lot!
                return
            else:
                logger.warn("Unhandled event: %s", event)
                return

            # Create a new record
            cursor.execute(sql, args)

            if cursor.rowcount != 1:
                logger.error('ERROR: Failed recording event: %s', event)

        # connection is not autocommit by default. So you must commit to save
        # your changes.
        connection.commit()
    except MySQLError as e:
        logger.error('ERROR: event %s: {!r}, errno is {}'.format(event, e, e.args[0]))
    finally:
        connection.close()


def send_email(subject, body, to_address):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = "tokensale@narrative.network"
    msg['To'] = to_address

    # Send the message via our own SMTP server.
    # bl: production servers user port 587
    #s = smtplib.SMTP('localhost',587)
    s = smtplib.SMTP('localhost')
    s.starttls()
    s.send_message(msg)
    s.quit()


def get_address(raw_address):
    # bl: NEO uses the AddressVersion 23, so stick that on the front of the address before doing the b58check
    raw_address = b'\x17' + raw_address
    return b58encode_check(raw_address)


def custom_background_code():
    """ Custom code run in a background thread. Prints the current block height.

    This function is run in a daemonized thread, which means it can be instantly killed at any
    moment, whenever the main thread quits. If you need more safety, don't use a  daemonized
    thread and handle exiting this thread in another way (eg. with signals and events).
    """
    while True:
        logger.info("Block %s / %s", str(Blockchain.Default().Height), str(Blockchain.Default().HeaderHeight))
        sleep(60)


def main():
    # Setup the blockchain
    settings.setup_privnet()
    global blockchain
    blockchain = LevelDBBlockchain(settings.LEVELDB_PATH)
    Blockchain.RegisterBlockchain(blockchain)
    NodeLeader.Instance().Start()
    dbloop = task.LoopingCall(Blockchain.Default().PersistBlocks)
    dbloop.start(.1)
    Blockchain.Default().PersistBlocks()

    # Disable smart contract events for external smart contracts
    settings.set_log_smart_contract_events(False)

    # Start a thread with custom code
    d = threading.Thread(target=custom_background_code)
    d.setDaemon(True)  # daemonizing the thread will kill it when the main thread is quit
    d.start()

    # Run all the things (blocking call)
    logger.info("Everything setup and running. Waiting for events...")
    reactor.run()
    logger.info("Shutting down")
    Blockchain.Default().Dispose()
    NodeLeader.Instance().Shutdown()
    logger.info("Shut down.")


if __name__ == "__main__":
    main()

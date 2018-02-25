"""
Based off of: https://github.com/CityOfZion/neo-python/blob/master/examples/smart-contract.py

Example of running a NEO node and receiving notifications when events
of a specific smart contract happen.

Events include Runtime.Notify, Runtime.Log, Storage.*, Execution.Success
and several more. See the documentation here:

http://neo-python.readthedocs.io/en/latest/smartcontracts.html

Usage:

* Update config/neo-nrve-config.json params
* Update config/network-wallets.json wallet path for the selected network
* Update config/db-config.json database details
* Update config/smtp-config.json SMTP details (host, port, TLS, etc.)
* Place this file in neo-python/neo/contrib and execute the following from neo-python dir:

python3.5 -m venv venv
source venv/bin/activate
# bl: these only need to be done once
#pip install -U setuptools pip wheel
#pip install -e .
python neo/contrib/neo-nrve-eventhandler.py

"""
import os
import json
from time import sleep

from neo.Core.Blockchain import Blockchain

from neo.contrib.smartcontract import SmartContract

from neo.contrib.narrative.blockchain.main import BlockchainMain, NetworkType

import pymysql.cursors
from pymysql import MySQLError

import smtplib
from email.mime.text import MIMEText

class TokenSaleEventHandler(BlockchainMain):

    smart_contract_hash = None

    # Setup the smart contract instance
    smart_contract = None
    old_smart_contract = None

    db_config = None
    smtp_config = None

    wallet_needs_recovery = False

    whitelists_to_process = []
    whitelist_tx_processing = None

    def __init__(self):
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'neo-nrve-config.json'), 'r') as f:
            config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'network-wallets.json'), 'r') as f:
            network_wallets_config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'db-config.json'), 'r') as f:
            self.db_config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'smtp-config.json'), 'r') as f:
            self.smtp_config = json.load(f)

        super().__init__(NetworkType[config['network']], 'neo-nrve-eventhandler')

        self.smart_contract_hash = config['smart_contract']
        self.smart_contract = SmartContract(self.smart_contract_hash)
        self.old_smart_contract = SmartContract(config['old_smart_contract'])

        # decorate the event handler method dynamically now that we have loaded the SCs
        self.sc_notify = self.old_smart_contract.on_notify(self.sc_notify)
        self.sc_notify = self.smart_contract.on_notify(self.sc_notify)

        self.setup_wallet(network_wallets_config[config['network']]['wallet_path'])

    def sc_notify(self, event):

        # Make sure that the event payload list has at least one element.
        if not len(event.event_payload):
            self.logger.info("[no event_payload] SmartContract Runtime.Notify event: %s", event)
            return

        if event.test_mode:
            self.logger.info("[test_mode] SmartContract Runtime.Notify event: %s", event)
            return

        if not event.execution_success:
            self.logger.info("[execution_success=false] SmartContract Runtime.Notify event: %s", event)
            return

        # The event payload list has at least one element. As developer of the smart contract
        # you should know what data-type is in the bytes, and how to decode it. In this example,
        # it's just a string, so we decode it with utf-8:
        event_type = event.event_payload[0].decode("utf-8")
        block_number = event.block_number

        timestamp = self.blockchain.GetHeaderByHeight(block_number).Timestamp

        # bl: event.contract_hash is a UInt160, so convert it to a hex string
        contract_hash = event.contract_hash.ToString()
        # bl: event.tx_hash is a UInt256, so convert it to a hex string
        tx_hash = event.tx_hash.ToString()

        # bl: we only care about refunds for the old smart contract
        if contract_hash == self.old_smart_contract.contract_hash and event_type != 'refund':
            return

        connection = self.get_connection()

        try:
            with connection.cursor() as cursor:
                if event_type == 'kyc_registration' or event_type == 'kyc_deregistration':
                    address = self.get_address(event.event_payload[1])
                    self.logger.info("- %s: %s", event_type, address)
                    sql = "update `NarrativeUserNeoAddress` set whitelisted = %s where neoAddress = %s"
                    args = (1 if event_type == 'kyc_registration' else 0, address)
                elif event_type == 'contribution':
                    # from, neo, tokens
                    address = self.get_address(event.event_payload[1])
                    # based on the smart contract, we know these should always be whole numbers
                    neo = (int)(event.event_payload[2] / 100000000)
                    tokens = (int)(event.event_payload[3] / 100000000)
                    self.logger.info("- %s: %s: %s NEO (%s NRVE) (tx: %s)", event_type, address, neo, tokens, tx_hash)
                    sql = ("insert into `NarrativeContribution` (transactionId, neo, nrveTokens, transactionDate, neoAddress_oid)\n"
                           "select %s, %s, %s, from_unixtime(%s), na.oid\n"
                           "from NarrativeUserNeoAddress na\n"
                           "where na.neoAddress = %s")
                    args = (tx_hash, neo, tokens, timestamp, address)
                elif event_type == 'refund':
                    # to, amount
                    address = self.get_address(event.event_payload[1])
                    # based on the smart contract, the amount should always be a whole number
                    amount = (int)(event.event_payload[2] / 100000000)
                    log = "%s: %s: %s NEO [%s] (tx: %s)" % (event_type, address, amount, contract_hash, tx_hash)
                    self.logger.info('- ' + log)
                    sql = ("insert into `NarrativeRefund` (transactionId, contractHash, neo, transactionDate, neoAddress)\n"
                           "values (%s, %s, %s, from_unixtime(%s), %s)")
                    args = (tx_hash, contract_hash, amount, timestamp, address)
                    self.send_email("Narrative Refund Required", log, "brian@narrative.network")
                elif event_type == 'transfer' or event_type == 'approve':
                    # bl: ignore NEP5 transfers and approvals. don't care about those, and there will be a lot!
                    return
                else:
                    self.logger.warn("Unhandled event: %s", event)
                    return

                # Create a new record
                cursor.execute(sql, args)

                if cursor.rowcount != 1:
                    self.logger.error('ERROR: Failed recording event: %s', event)

            # connection is not autocommit by default. So you must commit to save
            # your changes.
            connection.commit()
        except MySQLError as e:
            self.logger.error('ERROR: event %s: {!r}, errno is {}'.format(event, e, e.args[0]))
        finally:
            connection.close()

        # if this is the whitelist tx we are waiting for, then clear it out so the next can be processed!
        if tx_hash == self.whitelist_tx_processing:
            self.whitelist_tx_processing = None

    def get_connection(self):
        # Connect to the database
        return pymysql.connect(host=self.db_config['host'],
                               user=self.db_config['user'],
                               password=self.db_config['password'],
                               db=self.db_config['db'],
                               charset='utf8mb4',
                               cursorclass=pymysql.cursors.DictCursor)

    def send_email(self, subject, body, to_address):
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = "tokensale@narrative.network"
        msg['To'] = to_address

        # Send the message via our own SMTP server.
        # bl: production servers user port 587
        s = smtplib.SMTP(self.smtp_config['host'], self.smtp_config['port'])
        if self.smtp_config['use_tls']:
            s.starttls()
        s.send_message(msg)
        s.quit()

    def custom_background_code(self):
        count = 0
        while True:
            sleep(1)

            count += 1
            if (count % 60) == 0:
                self.logger.info("Block %s / %s", str(Blockchain.Default().Height), str(Blockchain.Default().HeaderHeight))
                count = 0

            # already have a whitelist that we are waiting to process? then just keep waiting until that transaction comes through
            if self.whitelist_tx_processing:
                continue

            # load addresses to whitelist every 15 seconds, but only if the list is empty
            if not self.whitelists_to_process:
                # look for NEO addresses to whitelist every 15 seconds
                if (count % 15) != 0:
                    continue
                self.load_addresses_to_whitelist()

            # no whitelists to process? then keep waiting
            if not self.whitelists_to_process:
                continue

            if self.wallet_needs_recovery:
                self.recover_wallet()
                self.wallet_needs_recovery = False
            else:
                self.wallet_sync()

            addresses_to_whitelist = self.whitelists_to_process[0:6]
            self.whitelists_to_process = self.whitelists_to_process[6:]

            self.logger.debug('whitelisting addresses: %s', addresses_to_whitelist)
            result = self.test_invoke([self.smart_contract_hash, 'crowdsale_register', str(addresses_to_whitelist)], len(addresses_to_whitelist), False)

            if not result:
                # transaction failed? wallet probably out-of-sync (insufficient funds) so reload it
                self.wallet_needs_recovery = True
                # we need to try to process this refund again, so add it back in to the list
                self.whitelists_to_process = addresses_to_whitelist + self.whitelists_to_process
            else:
                # transaction successfully relayed? then let's set the tx Hash that we're waiting for
                self.whitelist_tx_processing = result.Hash

    def load_addresses_to_whitelist(self):
        connection = self.get_connection()
        try:
            with connection.cursor() as cursor:
                sql = ("select na.neoAddress from `NarrativeUser` u\n"
                       "inner join `NarrativeUserNeoAddress` na on na.oid = u.primaryNeoAddress_oid\n"
                       "where na.whitelisted = 0\n"
                       "and u.hasVerifiedEmailAddress = 1\n"
                       "and u.kycStatus = 3;")
                cursor.execute(sql)

                rows = cursor.fetchall()

                for row in rows:
                    self.whitelists_to_process.append(row['neoAddress'])
        except MySQLError as e:
            self.logger.error('ERROR: selecting whitelist addresses: {!r}, errno is {}'.format(e, e.args[0]))
        finally:
            connection.close()

def main():
    event_handler = TokenSaleEventHandler()
    event_handler.run()


if __name__ == "__main__":
    main()

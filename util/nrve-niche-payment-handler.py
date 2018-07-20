"""
Based off of: https://github.com/CityOfZion/neo-python/blob/master/examples/smart-contract.py

Example of running a NEO node and receiving notifications when NRVE NEP-5 transfer events occur.

Events include Runtime.Notify, Runtime.Log, Storage.*, Execution.Success
and several more. See the documentation here:

http://neo-python.readthedocs.io/en/latest/smartcontracts.html

Usage:

* Update config/nrve-niche-config.json params
* Update config/network-wallets.json wallet path for the selected network
* Update config/db-config.json database details
* Place this file in neo-python/neo/contrib and execute the following from neo-python dir:

python3.5 -m venv venv
source venv/bin/activate
# bl: these only need to be done once
#pip install -U setuptools pip wheel
#pip install -e .
python neo/contrib/neo-niche-payment-handler.py

"""
import os
import json
import traceback
from time import sleep
import pymysql.cursors
from pymysql import MySQLError

import smtplib
from email.mime.text import MIMEText

from neo.Core.Blockchain import Blockchain

from neo.contrib.narrative.blockchain.main import BlockchainMain, NetworkType
from neo.contrib.smartcontract import SmartContract
from neo.SmartContract.ContractParameter import ContractParameter, ContractParameterType

from neo.Prompt.Commands.Tokens import do_token_transfer, get_asset_id


class NichePaymentHandler(BlockchainMain):

    # Setup the smart contract instance
    smart_contract = None

    nrve_token_symbol = "NRVE"
    niche_payment_address = None
    niche_payment_storage_address = None

    ignore_blocks_older_than = None

    db_config = None
    smtp_config = None

    wallet_needs_recovery = False

    transfers_to_process = []
    transfer_tx_processing = None

    def __init__(self):
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'nrve-niche-config.json'), 'r') as f:
            config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'network-wallets.json'), 'r') as f:
            network_wallets_config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'db-config.json'), 'r') as f:
            self.db_config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'smtp-config.json'), 'r') as f:
            self.smtp_config = json.load(f)

        super().__init__(NetworkType[config['network']], 'nrve-niche-payment-handler')

        self.smart_contract = SmartContract(config['smart_contract'])
        self.niche_payment_address = config['niche_payment_address']
        self.niche_payment_storage_address = config['niche_payment_storage_address']
        self.ignore_blocks_older_than = config['ignore_blocks_older_than']

        self.setup_wallet(network_wallets_config[config['network']]['wallet_path'])

        # decorate the event handler methods dynamically now that we have loaded the SC
        self.sc_notify = self.smart_contract.on_notify(self.sc_notify)

    def sc_notify(self, event):
        try:
            self.do_sc_notify(event)
        except Exception as e:
            print("Could not process notify event: %s" % e)
            traceback.print_stack()
            traceback.print_exc()
            raise e

    def do_sc_notify(self, event):

        event_payload = event.event_payload

        if not isinstance(event_payload, ContractParameter) or event_payload.Type != ContractParameterType.Array:
            self.logger.info("[invalid event_payload] SmartContract Runtime.Notify event: %s", event)
            return

        payload = event_payload.Value

        # Make sure that the event payload list has at least one element.
        if not len(payload):
            self.logger.info("[no event_payload] SmartContract Runtime.Notify event: %s", event)
            return

        if event.test_mode:
            self.logger.info("[test_mode] SmartContract Runtime.Notify event: %s", event)
            return

        if not event.execution_success:
            self.logger.info("[execution_success=false] SmartContract Runtime.Notify event: %s", event)
            return

        block_number = event.block_number

        if self.ignore_blocks_older_than and block_number < self.ignore_blocks_older_than:
            return

        # The event payload list has at least one element. As developer of the smart contract
        # you should know what data-type is in the bytes, and how to decode it. In this example,
        # it's just a string, so we decode it with utf-8:
        event_type = payload[0].Value.decode("utf-8")

        # only looking for transfer events, so ignore everything else
        if event_type != 'transfer':
            return

        self.logger.info("[event_payload] Processing event: %s", event)

        # from, to, amount
        from_address = self.get_address(payload[1].Value)
        to_address = self.get_address(payload[2].Value)
        raw_nrve_amount = payload[3].Value
        # bl: there can be different data types returned in the amount payload for some reason, so detect which it is (BigInteger/int or bytes)
        if isinstance(raw_nrve_amount, int):
            nrve_amount = raw_nrve_amount
        else:
            nrve_amount = int.from_bytes(raw_nrve_amount, 'little')

        # bl: event.tx_hash is a UInt256, so convert it to a hex string
        tx_hash = event.tx_hash.ToString()

        # if this is an outbound NRVE transfer from our payment wallet, then it's a transfer!
        if from_address == self.niche_payment_address:
            # in order to move on to the next transfer, we just need to clear the tx, assuming it's the right one!
            if self.transfer_tx_processing and tx_hash == self.transfer_tx_processing.ToString():
                if to_address == self.niche_payment_storage_address:
                    self.logger.info("- completed payment storage %s: to %s: %s NRVE (tx: %s)", event_type, to_address, nrve_amount, tx_hash)
                else:
                    self.logger.info("- completed refund %s: to %s: %s NRVE (tx: %s)", event_type, to_address, nrve_amount, tx_hash)
                self.transfer_tx_processing = None
            else:
                log = "%s: to %s: %s NRVE (tx: %s)" % (event_type, to_address, nrve_amount, tx_hash)
                self.logger.warn("- unexpected outbound transfer! %s", log)
                self.send_email("Unexpected Outbound Transfer", log)
            return

        # ignore transfers between other accounts. only care about payments to the niche payment address
        if to_address != self.niche_payment_address:
            self.logger.info("- ignoring unknown %s: to %s; not %s", event_type, to_address, self.niche_payment_address)
            return

        self.logger.info("Loading block %s", block_number)
        block = self.blockchain.GetHeaderByHeight(block_number)
        self.logger.info("Block %s loaded: %s" % (block_number, block))

        timestamp = block.Timestamp

        # Connect to the database
        connection = pymysql.connect(host=self.db_config['host'],
                                     user=self.db_config['user'],
                                     password=self.db_config['password'],
                                     db=self.db_config['db'],
                                     charset='utf8mb4',
                                     cursorclass=pymysql.cursors.DictCursor)

        try:
            with connection.cursor() as cursor:
                log = "- payment %s: from %s: %s NRVE (tx: %s)" % (event_type, from_address, nrve_amount, tx_hash)
                self.logger.info(log)
                sql = ("select oid from `NicheAuctionInvoicePayment`\n"
                       "where fromNeoAddress = %s\n"
                       "and nrveAmount = %s\n"
                       "and paymentStatus = 0\n"
                       "and transactionId is null\n"
                       "for update;")
                args = (from_address, nrve_amount)
                cursor.execute(sql, args)

                if cursor.rowcount == 0:
                    self.logger.error('Failed identifying payment. Returning to sender: %s', event)
                    self.transfer_payment(from_address, nrve_amount)
                    return
                elif cursor.rowcount > 1:
                    self.logger.error('FATAL! Identified multiple payments by unique key. Should not be possible! %s', event)
                    self.transfer_payment(from_address, nrve_amount)
                    return

                # when a payment is outstanding, it will be recorded with the expected from address, the proper nrveAmount (in "neurons")
                # and a paymentStatus of 0 which indicates it's pending payment
                sql = ("update `NicheAuctionInvoicePayment`\n"
                       "set transactionId = %s\n"
                       ", transactionDate = from_unixtime(%s)\n"
                       "where fromNeoAddress = %s\n"
                       "and nrveAmount = %s\n"
                       "and paymentStatus = 0\n"
                       "and transactionId is null;")
                args = (tx_hash, timestamp, from_address, nrve_amount)

                # Create a new record
                cursor.execute(sql, args)

                if cursor.rowcount != 1:
                    self.logger.error('Failed updating payment. Should not be possible since it was already locked for update: %s', event)
                    return

                # send the NRVE to the payment storage address
                self.transfer_payment(self.niche_payment_storage_address, nrve_amount)

                self.send_email("Successful Niche Payment", log)

            # connection is not autocommit by default. So you must commit to save
            # your changes.
            connection.commit()
        except MySQLError as e:
            error_message = 'ERROR: event %s: {!r}, errno is {}'.format(event, e, e.args[0])
            self.logger.error(error_message)
            self.send_email('Niche Payment Error', error_message)
        finally:
            connection.close()

    def send_email(self, subject, body):
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = self.smtp_config['from_address']
        msg['To'] = self.smtp_config['to_address']

        # Send the message via our own SMTP server.
        # bl: production servers user port 587
        s = smtplib.SMTP(self.smtp_config['host'], self.smtp_config['port'])
        if self.smtp_config['use_tls']:
            s.starttls()
        s.send_message(msg)
        s.quit()

    def transfer_payment(self, from_address, nrve_amount):
        self.transfers_to_process.append([from_address,nrve_amount])
        print('transfers_to_process %s', self.transfers_to_process)

    def custom_background_code(self):
        count = 0
        while True:
            sleep(1)

            count += 1
            if (count % 60)==0:
                self.logger.info("Block %s / %s", str(Blockchain.Default().Height), str(Blockchain.Default().HeaderHeight))
                count = 0

            # already have a transfer that we are waiting to process? then just keep waiting until that transaction comes through
            if self.transfer_tx_processing:
                continue

            # no transfers? then keep waiting
            if not self.transfers_to_process:
                continue

            if self.wallet_needs_recovery:
                self.recover_wallet()
                self.wallet_needs_recovery = False
            else:
                self.wallet_sync()

            transfer = self.transfers_to_process[0]
            self.transfers_to_process = self.transfers_to_process[1:]
            if len(transfer) != 2:
                self.logger.error('ERROR! transfer must have exactly 2 args. skipping! %s', transfer)
                continue

            to_address = transfer[0]
            if to_address == self.niche_payment_storage_address:
                self.logger.debug('processing payment storage: %s', transfer)
            else:
                self.logger.debug('processing refund: %s', transfer)
            token = get_asset_id(self.wallet, self.nrve_token_symbol)
            print('found token %s', token)
            result = do_token_transfer(token, self.wallet, self.niche_payment_address, to_address, transfer[1], False)

            if not result:
                # transaction failed? wallet probably out-of-sync (insufficient funds) so reload it
                self.wallet_needs_recovery = True
                # we need to try to process this transfer again, so add it back in to the list
                self.transfers_to_process = [transfer] + self.transfers_to_process
            else:
                # transaction successfully relayed? then let's set the tx Hash that we're waiting for
                self.transfer_tx_processing = result.Hash


def main():
    event_handler = NichePaymentHandler()
    event_handler.run()


if __name__ == "__main__":
    main()

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

from neocore import BigInteger

from neo.Core.Blockchain import Blockchain

from neo.contrib.narrative.blockchain.main import BlockchainMain, NetworkType
from neo.contrib.smartcontract import SmartContract

from neo.Prompt.Commands.Tokens import token_send, get_asset_id, string_from_amount


class NichePaymentHandler(BlockchainMain):

    # Setup the smart contract instance
    smart_contract = None

    nrve_token_symbol = "NRVE"
    niche_payment_address = None

    db_config = None

    wallet_needs_recovery = False

    refunds_to_process = []
    refund_tx_processing = None

    def __init__(self):
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'nrve-niche-config.json'), 'r') as f:
            config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'network-wallets.json'), 'r') as f:
            network_wallets_config = json.load(f)
        with open(os.path.join(os.path.abspath(os.path.dirname(__file__)), 'config', 'db-config.json'), 'r') as f:
            self.db_config = json.load(f)

        super().__init__(NetworkType[config['network']], 'nrve-niche-payment-handler')

        self.smart_contract = SmartContract(config['smart_contract'])
        self.niche_payment_address = config['niche_payment_address']

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

        # only looking for transfer events, so ignore everything else
        if event_type != 'transfer':
            return

        # from, to, amount
        from_address = self.get_address(event.event_payload[1])
        to_address = self.get_address(event.event_payload[2])
        raw_nrve_amount = event.event_payload[3]
        # bl: it seems our TestNet contract vs. MainNet contracts are somehow returning different payload values, so detect which
        if type(raw_nrve_amount) is BigInteger:
            nrve_amount = raw_nrve_amount
        else:
            nrve_amount = raw_nrve_amount.GetBigInteger()

        # bl: event.tx_hash is a UInt256, so convert it to a hex string
        tx_hash = event.tx_hash.ToString()

        # if this is an outbound NRVE transfer from our payment wallet, then it's a refund!
        if from_address == self.niche_payment_address:
            # in order to move on to the next refund, we just need to clear the tx, assuming it's the right one!
            if tx_hash == self.refund_tx_processing.ToString():
                self.logger.info("- refund %s: to %s: %s NRVE (tx: %s)", event_type, to_address, nrve_amount, tx_hash)
                self.refund_tx_processing = None
            else:
                self.logger.warn("- unexpected refund! %s: to %s: %s NRVE (tx: %s)", event_type, to_address, nrve_amount, tx_hash)
            return

        # ignore transfers between other accounts. only care about payments to the niche payment address
        if to_address != self.niche_payment_address:
            return

        block_number = event.block_number

        timestamp = self.blockchain.GetHeaderByHeight(block_number).Timestamp

        # Connect to the database
        connection = pymysql.connect(host=self.db_config['host'],
                                     user=self.db_config['user'],
                                     password=self.db_config['password'],
                                     db=self.db_config['db'],
                                     charset='utf8mb4',
                                     cursorclass=pymysql.cursors.DictCursor)

        try:
            with connection.cursor() as cursor:
                self.logger.info("- payment %s: from %s: %s NRVE (tx: %s)", event_type, from_address, nrve_amount, tx_hash)
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
                    self.refund_payment(from_address, nrve_amount)
                    return
                elif cursor.rowcount > 1:
                    self.logger.error('FATAL! Identified multiple payments by unique key. Should not be possible! %s', event)
                    self.refund_payment(from_address, nrve_amount)
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

            # connection is not autocommit by default. So you must commit to save
            # your changes.
            connection.commit()
        except MySQLError as e:
            self.logger.error('ERROR: event %s: {!r}, errno is {}'.format(event, e, e.args[0]))
        finally:
            connection.close()

    def refund_payment(self, from_address, nrve_amount):
        token = get_asset_id(self.wallet, self.nrve_token_symbol)
        print('found token %s', token)
        # the nrve_amount that comes from the NEP-5 "transfer" event is in the smallest NRVE units (0.00000001).
        # this method easily converts that amount into the equivalent value that would be entered at the neo-python command line
        nrve_amount_str = string_from_amount(token, nrve_amount)
        print('nrve_amount_str %s', nrve_amount_str)
        self.refunds_to_process.append([self.nrve_token_symbol,self.niche_payment_address,from_address,nrve_amount_str])
        print('refunds_to_process %s', self.refunds_to_process)

    def custom_background_code(self):
        count = 0
        while True:
            sleep(1)

            count += 1
            if (count % 60)==0:
                self.logger.info("Block %s / %s", str(Blockchain.Default().Height), str(Blockchain.Default().HeaderHeight))
                count = 0

            # already have a refund that we are waiting to process? then just keep waiting until that transaction comes through
            if self.refund_tx_processing:
                continue

            # no refunds? then keep waiting
            if not self.refunds_to_process:
                continue

            if self.wallet_needs_recovery:
                self.recover_wallet()
                self.wallet_needs_recovery = False
            else:
                self.wallet_sync()

            refund = self.refunds_to_process[0]
            self.refunds_to_process = self.refunds_to_process[1:]
            if len(refund) != 4:
                self.logger.error('ERROR! refunds must have exactly 4 args. skipping! %s', refund)
                continue

            self.logger.debug('processing refund: %s', refund)
            result = token_send(self.wallet, refund, False)

            if not result:
                # transaction failed? wallet probably out-of-sync (insufficient funds) so reload it
                self.wallet_needs_recovery = True
                # we need to try to process this refund again, so add it back in to the list
                self.refunds_to_process = [refund] + self.refunds_to_process
            else:
                # transaction successfully relayed? then let's set the tx Hash that we're waiting for
                self.refund_tx_processing = result.Hash


def main():
    event_handler = NichePaymentHandler()
    event_handler.run()


if __name__ == "__main__":
    main()

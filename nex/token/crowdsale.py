from boa.blockchain.vm.Neo.Blockchain import GetHeight,GetHeader
from boa.blockchain.vm.Neo.Action import RegisterAction
from boa.blockchain.vm.Neo.Runtime import Notify,CheckWitness
from boa.code.builtins import concat
from nex.token.mytoken import Token
from nex.common.storage import StorageAPI
from nex.common.txio import Attachments,get_asset_attachments

OnTransfer = RegisterAction('transfer', 'from', 'to', 'amount')
OnRefund = RegisterAction('refund', 'to', 'amount')

OnInvalidKYCAddress = RegisterAction('invalid_registration','address')
OnKYCRegister = RegisterAction('kyc_registration','address')


class Crowdsale():

    kyc_key = b'kyc_ok'

    # February 9, 2018 @ 9:00:00 pm UTC
    presale_end = 1518210000
    presale_phase_key = b'r1'
    presale_individual_limit = 10000 * 100000000
    presale_tokens_per_neo = 400 * 100000000
    presale_minimum = 800 * 100000000
    presale_token_limit = 20000000 * 100000000  # 40% of 50m total supply = 20m * 10^8 ( decimals)

    # February 13, 2018 @ 5:00:00 pm UTC
    day1_start = 1518541200
    day1_phase_key = b'r2'
    day1_individual_limit = 1000 * 100000000
    day1_tokens_per_neo = 333 * 100000000

    # February 14, 2018 @ 5:00:00 pm UTC
    day2_start = 1518627600
    day2_phase_key = b'r3'
    day2_individual_limit = 5000 * 100000000
    day2_tokens_per_neo = 315 * 100000000

    # February 15, 2018 @ 4:59:59 pm UTC
    day2_end = 1518713999
    sale_tokens_per_neo = 300 * 100000000

    # March 22, 2018 @ 5:00:00 pm UTC
    sale_end = 1521738000

    def kyc_register(self, args, token:Token):
        """

        :param args:list a list of addresses to register
        :param token:Token A token object with your ICO settings
        :return:
            int: The number of addresses to register for KYC
        """
        ok_count = 0

        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if CheckWitness(owner):

            for address in args:

                if len(address) == 20:

                    kyc_storage_key = concat(self.kyc_key, address)
                    storage.put(kyc_storage_key, True)

                    OnKYCRegister(address)
                    ok_count += 1

        return ok_count


    def kyc_status(self, args):
        """
        Gets the KYC Status of an address

        :param args:list a list of arguments
        :return:
            bool: Returns the kyc status of an address
        """
        storage = StorageAPI()

        if len(args) > 0:
            addr = args[0]

            kyc_storage_key = concat(self.kyc_key, addr)

            return storage.get(kyc_storage_key)

        return False



    def exchange(self, token: Token):
        """

        :param token:Token The token object with NEP5/sale settings
        :return:
            bool: Whether the exchange was successful
        """

        attachments = get_asset_attachments()  # type:  Attachments

        storage = StorageAPI()

        # this looks up whether the exchange can proceed
        tokens = self.check_and_calculate_tokens(token, attachments, storage)

        if tokens <= 0:
            print("Cannot exchange value")
            # This should only happen in the case that there are a lot of TX on the final
            # block before the total amount is reached.  An amount of TX will get through
            # the verification phase because the total amount cannot be updated during that phase
            # because of this, there should be a process in place to manually refund tokens
            OnRefund(attachments.sender_addr, attachments.neo_attached)
            return False


        # lookup the current balance of the address
        current_balance = storage.get(attachments.sender_addr)

        # add it to the the exchanged tokens and persist in storage
        new_total = tokens + current_balance
        storage.put(attachments.sender_addr, new_total)

        # update the in circulation amount
        token.add_to_circulation(tokens, storage)

        # dispatch transfer event
        OnTransfer(attachments.receiver_addr, attachments.sender_addr, tokens)

        return True


    def check_and_calculate_tokens(self, token:Token, attachments:Attachments, storage:StorageAPI):
        """
        Determines if the contract invocation meets all requirements for the ICO exchange
        of neo into NEP5 Tokens.
        Note: This method can be called via both the Verification portion of an SC or the Application portion

        When called in the Verification portion of an SC, it can be used to reject TX that do not qualify
        for exchange, thereby reducing the need for manual NEO refunds considerably

        :param token:Token A token object with your ICO settings
        :param attachments:Attachments An attachments object with information about attached NEO/Gas assets
        :param storage:StorageAPI A StorageAPI object for storage interaction
        :return:
            int: Total amount of tokens to distribute, or 0 if this isn't a valid contribution
        """

        if attachments.neo_attached == 0:
            print("no neo attached")
            return 0

        # the following looks up whether an address has been
        # registered with the contract for KYC regulations
        is_kyc_approved = self.get_kyc_status(attachments.sender_addr, storage)
        if not is_kyc_approved:
            print("not KYC approved")
            return 0

        # it turns out this is REQUIRED to work around a neo-boa python compiler issue: https://github.com/CityOfZion/neo-boa/issues/29
        #else:
        #    print("KYC approved")
        j = 0

        return self.calculate_tokens(token, attachments.neo_attached, attachments.sender_addr)


    def get_kyc_status(self, address, storage:StorageAPI):
        """
        Looks up the KYC status of an address

        :param address:bytearray The address to lookup
        :param storage:StorageAPI A StorageAPI object for storage interaction
        :return:
            bool: KYC Status of address
        """
        kyc_storage_key = concat(self.kyc_key, address)

        return storage.get(kyc_storage_key)

    def calculate_tokens(self, token: Token, neo_attached: int, address):
        """
        Perform custom token exchange calculations here.

        :param token:Token The token settings for the sale
        :param amount:int Number of NEO to convert to tokens
        :param address:bytearray The address to mint the tokens to
        :return:
            int: Total amount of tokens to distribute, or 0 if this isn't a valid contribution
        """
        height = GetHeight()
        currentBlock = GetHeader(height)
        time = currentBlock.Timestamp

        if time > self.sale_end:
            print("crowdsale ended")
            return 0

        # in all phases except the presale, the limit for tokens in circulation is the sale token limit of 50m
        tokens_in_circulation_limit = token.sale_token_limit

        # if we are in main sale, post-day 2, then any contribution is allowed
        if time > self.day2_end:
            phase_key_prefix = None
            individual_limit = -1
            tokens_per_neo = self.sale_tokens_per_neo
        elif time >= self.day2_start:
            phase_key_prefix = self.day2_phase_key
            individual_limit = self.day2_individual_limit
            tokens_per_neo = self.day2_tokens_per_neo
        elif time >= self.day1_start:
            phase_key_prefix = self.day1_phase_key
            individual_limit = self.day1_individual_limit
            tokens_per_neo = self.day1_tokens_per_neo
        elif time > self.presale_end:
            print("presale over, main sale not started")
            return 0
        else:
            if neo_attached < self.presale_minimum:
                print("insufficient presale contribution")
                return 0
            # for the presale, the total tokens in circulation are limited to 20m
            tokens_in_circulation_limit = self.presale_token_limit
            phase_key_prefix = self.presale_phase_key
            individual_limit = self.presale_individual_limit
            tokens_per_neo = self.presale_tokens_per_neo

        # this value will always be an int value, but is converted to float by the division. cast back to int, which should always be safe.
        # note that the neo_attached has a value mirroring GAS. so, even though NEO technically doesn't have any decimals of precision,
        # the value still needs to be divided to get down to the whole NEO unit
        tokens = neo_attached / 100000000 * tokens_per_neo

        storage = StorageAPI()

        tokens_in_circulation = storage.get(token.in_circulation_key)

        new_tokens_in_circulation = tokens_in_circulation + tokens

        if new_tokens_in_circulation > tokens_in_circulation_limit:
            print("amount greater than token sale limit")
            return 0

        # in the main sale, all contributions are allowed, up to the tokens in circulation limit defined above
        if individual_limit <= 0:
            # note that we do not need to store the contribution at this point since there is no limit
            return tokens

        if neo_attached <= individual_limit:

            # check if they have already exchanged in the limited round
            phase_key = concat(phase_key_prefix, address)

            total_amount_contributed = storage.get(phase_key)

            # add on the amount of the new contribution
            total_amount_contributed += neo_attached

            # if the total amount is less than the individual limit, they're good!
            if total_amount_contributed <= individual_limit:
                storage.put(phase_key, total_amount_contributed)
                return tokens

            print("contribution limit exceeded in round")
            return 0

        print("too much for limited round")

        return 0


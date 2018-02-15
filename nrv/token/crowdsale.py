from boa.blockchain.vm.Neo.Blockchain import GetHeight,GetHeader
from boa.blockchain.vm.Neo.Action import RegisterAction
from boa.blockchain.vm.Neo.Runtime import Notify,CheckWitness
from boa.code.builtins import concat, substr
from nrv.token.nrvtoken import Token
from nrv.common.storage import StorageAPI
from nrv.common.txio import Attachments,get_asset_attachments
from nrv.common.time import get_now

OnTransfer = RegisterAction('transfer', 'from', 'to', 'amount')
OnContribution = RegisterAction('contribution', 'from', 'neo', 'tokens')
OnRefund = RegisterAction('refund', 'to', 'amount')

OnKYCRegister = RegisterAction('kyc_registration','address')
OnKYCDeregister = RegisterAction('kyc_deregistration','address')


class Crowdsale():

    kyc_key = b'kyc_ok'

    # February 9, 2018 @ 9:00:00 pm UTC
    presale_end_block_key = b'pre_end'
    presale_phase_key = b'r1'
    presale_individual_limit = 3000
    presale_tokens_per_neo = 400 * 100000000
    presale_minimum = 800
    presale_token_limit = 20220000 * 100000000  # 50,550 NEO * 400 NRV/NEO = 20.22m * 10^8 (decimals)

    # the number of blocks per day, assuming 23 seconds/block
    blocks_per_day = 3757  # 24 * 60 * 60 / 23

    # February 20, 2018 @ 5:00:00 pm UTC
    pub_sale_start_block_key = b'pub_start'
    day1_phase_key = b'r2'
    day1_individual_limit = 300 * 100000000
    day1_tokens_per_neo = 333 * 100000000

    # February 21, 2018 @ 5:00:00 pm UTC
    day2_phase_key = b'r3'
    day2_individual_limit = 1000 * 100000000
    day2_tokens_per_neo = 315 * 100000000

    # February 22, 2018 @ 4:59:59 pm UTC
    sale_tokens_per_neo = 300 * 100000000

    # March 29, 2018 @ 9:00:00 pm UTC
    sale_end = 1522357200
    # sale lasts 37 days. assume 23 seconds per block
    sale_blocks = 138992  # 37 * 24 * 60 * 60 / 23

    team_tokens_max = 20000000 * 100000000  # 20m team tokens * 10^8 (decimals)
    team_token_distribution_key = b'team_tokens'

    # January 1, 2019 00:00 UTC
    initial_team_vest_date = 1546300800

    company_tokens_max = 30000000 * 100000000  # 30m company tokens * 10^8 (decimals)
    company_token_distribution_key = b'company_tokens'

    rewards_fund_tokens_max = 97500000 * 100000000  # 97.5m tokens can be minted for the rewards fund * 10^8 (decimals)
    rewards_fund_token_distribution_key = b'rewards_fund'

    def end_pre_sale(self, token: Token):
        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if not CheckWitness(owner):
            return False

        presale_end_block = storage.get(self.presale_end_block_key)

        if presale_end_block:
            print("can't end the pre-sale twice")
            return False

        tokens_in_circulation = storage.get(token.in_circulation_key)

        # enforce that all of the pre-sale contributions have been distributed before the pre-sale can be ended
        if tokens_in_circulation != self.presale_token_limit:
            print("can't end the pre-sale before distributing all pre-sale tokens")
            return False

        height = GetHeight()

        storage.put(self.presale_end_block_key, height)

        return True

    def start_public_sale(self, token: Token):
        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if not CheckWitness(owner):
            return False

        presale_end_block = storage.get(self.presale_end_block_key)
        if not presale_end_block:
            print("can't start the public sale until pre-sale has ended")
            return False

        pub_sale_start_block = storage.get(self.pub_sale_start_block_key)

        if pub_sale_start_block:
            print("can't start the public sale twice")
            return False

        height = GetHeight()

        storage.put(self.pub_sale_start_block_key, height)

        return True

    def kyc_register(self, args, token: Token):
        """

        :param args:list a list of addresses to register
        :param token: Token A token object with your ICO settings
        :return:
            int: The number of addresses registered for KYC
        """
        ok_count = 0

        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if CheckWitness(owner):

            for addresses in args:

                # bl: allowing multiple addresses to be encoded into a single parameter. this works around
                # the limitation of list arguments only supporting at most 16 elements
                # can be passed in as follows:
                # testinvoke {script_hash} crowdsale_register [bytearray(b'\x015\x829\x8cm6f\xb3\xac\xcc\xcas\x1dw\x06\xbc\xd2\x9co#\xba\'\x03\xc52c\xe8\xd6\xe5"\xdc2\x2039\xdc\xd8\xee\xe9')]
                # note that neo-python doesn't like spaces in the strings, so convert any spaces to the hex equivalent: '\x20'
                addr_length = len(addresses)

                # addresses are 20 bytes, so the length must be a multiple of 20 or else it's invalid!
                if (addr_length % 20) != 0:
                    continue

                addr_count = addr_length / 20

                i = 0
                while i < addr_count:
                    start = i * 20
                    address = substr(addresses, start, 20)

                    kyc_storage_key = concat(self.kyc_key, address)
                    storage.put(kyc_storage_key, True)

                    OnKYCRegister(address)
                    ok_count += 1
                    i += 1

        return ok_count

    def kyc_deregister(self, args, token: Token):
        """

        :param args:list a list of addresses to deregister
        :param token: Token A token object with your ICO settings
        :return:
            int: The number of addresses deregistered from KYC
        """
        ok_count = 0

        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if CheckWitness(owner):

            for address in args:

                if len(address) == 20:

                    kyc_storage_key = concat(self.kyc_key, address)
                    storage.delete(kyc_storage_key)

                    OnKYCDeregister(address)
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

            return self.get_kyc_status(addr, storage)

        return False

    def exchange(self, token: Token):
        """
        Make a token sale contribution to exchange NEO for NRV
        :param token: Token The token object with NEP5/sale settings
        :return:
            bool: Whether the exchange was successful
        """

        attachments = get_asset_attachments()  # type:  Attachments

        storage = StorageAPI()

        # this looks up whether the exchange can proceed
        tokens = self.check_and_calculate_tokens(token, attachments, storage, False)

        if tokens <= 0:
            print("Cannot exchange value")
            # This should only happen in the case that there are a lot of TX on the final
            # block before the total amount is reached.  An amount of TX will get through
            # the verification phase because the total amount cannot be updated during that phase
            # because of this, there should be a process in place to manually refund tokens
            if attachments.neo_attached > 0:
                OnRefund(attachments.sender_addr, attachments.neo_attached)
            return False

        self.mint_tokens(token, attachments.receiver_addr, attachments.sender_addr, tokens, storage)

        # track contributions as a separate event for token sale account page transaction updates
        OnContribution(attachments.sender_addr, attachments.neo_attached, tokens)

        return True

    def check_and_calculate_tokens(self, token: Token, attachments: Attachments, storage: StorageAPI, verify_only: bool):
        """
        Determines if the contract invocation meets all requirements for the ICO exchange
        of neo into NEP5 Tokens.
        Note: This method can be called via both the Verification portion of an SC or the Application portion

        When called in the Verification portion of an SC, it can be used to reject TX that do not qualify
        for exchange, thereby reducing the need for manual NEO refunds considerably

        :param token: Token A token object with your ICO settings
        :param attachments: Attachments An attachments object with information about attached NEO/Gas assets
        :param storage: StorageAPI A StorageAPI object for storage interaction
        :param verify_only: boolean to indicate whether we are only verifying the tx.
               when verifying, we will skip any put side effects.
        :return:
            int: Total amount of tokens to distribute, or 0 if this isn't a valid contribution
        """

        # don't allow any contributions if the sale is paused
        if storage.get(token.sale_paused_key):
            return 0

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
        # else:
        #    print("KYC approved")
        j = 0

        return self.calculate_tokens(token, attachments.neo_attached, attachments.sender_addr, verify_only)

    def get_kyc_status(self, address, storage: StorageAPI):
        """
        Looks up the KYC status of an address

        :param address:bytearray The address to lookup
        :param storage: StorageAPI A StorageAPI object for storage interaction
        :return:
            bool: KYC Status of address
        """

        if len(address) == 20:
            kyc_storage_key = concat(self.kyc_key, address)

            if storage.get(kyc_storage_key):
                return True

        return False

    def calculate_tokens(self, token: Token, neo_attached: int, address, verify_only: bool):
        """
        Perform custom token exchange calculations here.

        :param token: Token The token settings for the sale
        :param neo_attached: int Number of NEO to convert to tokens
        :param address: bytearray The address to mint the tokens to
        :param verify_only: boolean to indicate whether we are only verifying the tx.
               when verifying, we will skip any put side effects.
        :return:
            int: Total amount of tokens to distribute, or 0 if this isn't a valid contribution
        """
        height = GetHeight()

        storage = StorageAPI()

        # in all phases except the presale, the limit for tokens in circulation is the sale token limit of 50m
        tokens_in_circulation_limit = token.sale_token_limit

        presale_end_block = storage.get(self.presale_end_block_key)
        pub_sale_start_block = storage.get(self.pub_sale_start_block_key)

        if not presale_end_block:
            # not accepting user contributions during the pre-sale phase now
            print("presale contributions not allowed")
            return 0
        elif not pub_sale_start_block:
            print("presale over, main sale not started")
            return 0
        elif height > (pub_sale_start_block + self.sale_blocks):
            print("crowdsale ended")
            return 0
        elif height > (pub_sale_start_block + (2*self.blocks_per_day)):
            # if we are in main sale, post-day 2, then any contribution is allowed
            phase_key_prefix = None
            individual_limit = -1
            tokens_per_neo = self.sale_tokens_per_neo
        elif height > (pub_sale_start_block + self.blocks_per_day):
            phase_key_prefix = self.day2_phase_key
            individual_limit = self.day2_individual_limit
            tokens_per_neo = self.day2_tokens_per_neo
        else:
            phase_key_prefix = self.day1_phase_key
            individual_limit = self.day1_individual_limit
            tokens_per_neo = self.day1_tokens_per_neo

        # this value will always be an int value, but is converted to float by the division. cast back to int, which should always be safe.
        # note that the neo_attached has a value mirroring GAS. so, even though NEO technically doesn't have any decimals of precision,
        # the value still needs to be divided to get down to the whole NEO unit
        tokens = neo_attached / 100000000 * tokens_per_neo

        tokens_in_circulation = storage.get(token.in_circulation_key)

        new_tokens_in_circulation = tokens_in_circulation + tokens

        if new_tokens_in_circulation > tokens_in_circulation_limit:
            print("purchase would exceed token sale limit")
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
                # note that this method can be invoked during the Verification trigger, so we have the
                # verify_only param to avoid the Storage.Put during the read-only Verification trigger.
                # this works around a "method Neo.Storage.Put not found in ->" error in InteropService.py
                # since Verification is read-only and thus uses a StateReader, not a StateMachine
                if not verify_only:
                    storage.put(phase_key, total_amount_contributed)
                return tokens

            print("contribution limit exceeded in round")
            return 0

        print("too much for limited round")

        return 0

    def mint_tokens(self, token: Token, from_address, to_address, tokens, storage: StorageAPI):
        """
        Mint tokens for an address
        :param token: the token being minted
        :param from_address: the address from which the tokens are being minted (should always be the contract address)
        :param to_address: the address to transfer the minted tokens to
        :param tokens: the number of tokens to mint
        :param storage: StorageAPI
        """
        # lookup the current balance of the address
        current_balance = storage.get(to_address)

        # add it to the exchanged tokens and persist in storage
        new_total = tokens + current_balance
        storage.put(to_address, new_total)

        # update the in circulation amount
        token.add_to_circulation(tokens, storage)

        # dispatch transfer event
        OnTransfer(from_address, to_address, tokens)

    def transfer_presale_tokens(self, token: Token, args):
        """
        Transfer pre-sale tokens to a wallet address according to the 800 NEO minimum and 3,000 NEO maximum individual limits
        :param token: the token being minted for the team
        :param args: the address and number of neo for the contribution
        :return: True if successful
        """
        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if not CheckWitness(owner):
            return False

        if len(args) != 2:
            return False

        address = args[0]
        neo = args[1]

        if len(address) != 20:
            return False
        if neo <= 0:
            return False

        # stop presale token distribution once the pre-sale is over
        presale_end_block = storage.get(self.presale_end_block_key)
        if presale_end_block:
            print("can't distribute pre-sale tokens once the pre-sale has ended")
            return False

        tokens_in_circulation = storage.get(token.in_circulation_key)

        max_neo_remaining = (self.presale_token_limit - tokens_in_circulation) / self.presale_tokens_per_neo

        # calculate the number of tokens based on the neo value supplied
        tokens = neo * self.presale_tokens_per_neo

        new_tokens_in_circulation = tokens_in_circulation + tokens

        # don't allow more than the presale token limit to be distributed
        if new_tokens_in_circulation > self.presale_token_limit:
            print("transfer would exceed presale token limit")
            return 0

        # protect against scenarios where we could deadlock the contract by making
        # a mistake in our manual distribution. allow amount smaller than 800 NEO
        # if we're down to fewer than 800 NEO remaining to close the pre-sale
        if neo < self.presale_minimum and self.presale_minimum < max_neo_remaining:
            print("insufficient presale contribution")
            return 0

        # check if they have already exchanged in the limited round
        phase_key = concat(self.presale_phase_key, address)

        total_amount_contributed = storage.get(phase_key)

        # add on the amount of the new contribution
        total_amount_contributed += neo

        if total_amount_contributed > self.presale_individual_limit:
            print("transfer would exceed presale individual limit")
            return 0

        storage.put(phase_key, total_amount_contributed)

        attachments = get_asset_attachments()  # type:  Attachments

        #self.mint_tokens(token, attachments.receiver_addr, address, tokens, storage)
        from_address = attachments.receiver_addr
        to_address = address

        # bl: the following is an exact copy of the mint_tokens function. invoking self.mint_tokens will break the
        # execution of this method due to a neo-boa compiler issue. this results in a lot of code duplication,
        # but it's preferable to the alternative of a broken smart contract. refer: https://github.com/CityOfZion/neo-boa/issues/40

        # lookup the current balance of the address
        current_balance = storage.get(to_address)

        # add it to the exchanged tokens and persist in storage
        new_total = tokens + current_balance
        storage.put(to_address, new_total)

        # update the in circulation amount
        token.add_to_circulation(tokens, storage)

        # dispatch transfer event
        OnTransfer(from_address, to_address, tokens)

        return True

    def transfer_team_tokens(self, token: Token, args):
        """
        Transfer team tokens to a wallet address according to the 3-year team token vesting schedule
        :param token: the token being minted for the team
        :param args: the address and number of tokens to mint
        :return: True if successful
        """
        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if not CheckWitness(owner):
            return False

        if len(args) != 2:
            return False

        address = args[0]
        tokens = args[1]

        if len(address) != 20:
            return False
        if tokens <= 0:
            return False

        now = get_now()

        # no team token distribution until initial team vest date at the earliest
        if now < self.initial_team_vest_date:
            print("can't transfer_team_tokens before vesting date")
            return False

        seconds_in_year = 31536000

        # in the first year, allow 30% token distribution
        if now < (self.initial_team_vest_date + seconds_in_year):
            max_token_distribution = self.team_tokens_max * 3 / 10
        # in the second year, allow 60% total token distribution
        elif now < (self.initial_team_vest_date + (2*seconds_in_year)):
            max_token_distribution = self.team_tokens_max * 6 / 10
        # in the third year, allow 80% total token distribution
        elif now < (self.initial_team_vest_date + (3*seconds_in_year)):
            max_token_distribution = self.team_tokens_max * 8 / 10
        # beyond the third year, allow 100% total token distribution
        else:
            max_token_distribution = self.team_tokens_max

        team_tokens_distributed = storage.get(self.team_token_distribution_key)

        team_tokens_distributed += tokens

        # don't allow more than the max tokens to be distributed
        if team_tokens_distributed > max_token_distribution:
            print("can't exceed transfer_team_tokens vesting limit")
            return False

        storage.put(self.team_token_distribution_key, team_tokens_distributed)

        attachments = get_asset_attachments()  # type:  Attachments

        #self.mint_tokens(token, attachments.receiver_addr, address, tokens, storage)
        from_address = attachments.receiver_addr
        to_address = address

        # bl: the following is an exact copy of the mint_tokens function. invoking self.mint_tokens will break the
        # execution of this method due to a neo-boa compiler issue. this results in a lot of code duplication,
        # but it's preferable to the alternative of a broken smart contract. refer: https://github.com/CityOfZion/neo-boa/issues/40

        # lookup the current balance of the address
        current_balance = storage.get(to_address)

        # add it to the exchanged tokens and persist in storage
        new_total = tokens + current_balance
        storage.put(to_address, new_total)

        # update the in circulation amount
        token.add_to_circulation(tokens, storage)

        # dispatch transfer event
        OnTransfer(from_address, to_address, tokens)

        return True

    def transfer_company_tokens(self, token: Token, args):
        """
        Transfer company tokens to a wallet address according to the 2-year company token vesting schedule
        :param token: the token being minted for the company
        :param args: the address and number of tokens to mint
        :return: True if successful
        """
        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if not CheckWitness(owner):
            return False

        if len(args) != 2:
            return False

        address = args[0]
        tokens = args[1]

        if len(address) != 20:
            return False
        if tokens <= 0:
            return False

        now = get_now()

        seconds_in_year = 31536000

        # no company token distribution until after the ICO ends
        if now < self.sale_end:
            print("can't transfer_company_tokens before sale ends")
            return False

        # in the first year, allow 50% token distribution
        if now < (self.sale_end + seconds_in_year):
            max_token_distribution = self.company_tokens_max * 5 / 10
        # in the second year, allow 75% total token distribution
        elif now < (self.sale_end + (2*seconds_in_year)):
            max_token_distribution = self.company_tokens_max * 75 / 100
        # beyond the second year, allow 100% total token distribution
        else:
            max_token_distribution = self.company_tokens_max

        company_tokens_distributed = storage.get(self.company_token_distribution_key)

        company_tokens_distributed += tokens

        # don't allow more than the max tokens to be distributed
        if company_tokens_distributed > max_token_distribution:
            print("can't exceed transfer_company_tokens vesting limit")
            return False

        storage.put(self.company_token_distribution_key, company_tokens_distributed)

        attachments = get_asset_attachments()  # type:  Attachments

        #self.mint_tokens(token, attachments.receiver_addr, address, tokens, storage)
        from_address = attachments.receiver_addr
        to_address = address

        # bl: the following is an exact copy of the mint_tokens function. invoking self.mint_tokens will break the
        # execution of this method due to a neo-boa compiler issue. this results in a lot of code duplication,
        # but it's preferable to the alternative of a broken smart contract. refer: https://github.com/CityOfZion/neo-boa/issues/40

        # lookup the current balance of the address
        current_balance = storage.get(to_address)

        # add it to the exchanged tokens and persist in storage
        new_total = tokens + current_balance
        storage.put(to_address, new_total)

        # update the in circulation amount
        token.add_to_circulation(tokens, storage)

        # dispatch transfer event
        OnTransfer(from_address, to_address, tokens)

        return True

    def mint_rewards_tokens(self, token: Token, args):
        """
        Mint tokens for the rewards pool
        :param token: the token being minted for the rewards pool
        :param args: the address and number of tokens to mint
        :return: True if successful
        """
        storage = StorageAPI()

        owner = storage.get(token.owner_key)
        if not CheckWitness(owner):
            return False

        if len(args) != 2:
            return False

        address = args[0]
        tokens = args[1]

        if len(address) != 20:
            return False
        if tokens <= 0:
            return False

        now = get_now()

        # no minting rewards tokens until after the token sale ends
        if now < self.sale_end:
            print("can't mint_rewards_tokens before sale ends")
            return False

        rewards_fund_tokens_distributed = storage.get(self.rewards_fund_token_distribution_key)

        rewards_fund_tokens_distributed += tokens

        # don't allow more than the max tokens to be distributed
        if rewards_fund_tokens_distributed > self.rewards_fund_tokens_max:
            print("can't exceed mint_rewards_tokens limit")
            return False

        storage.put(self.rewards_fund_token_distribution_key, rewards_fund_tokens_distributed)

        attachments = get_asset_attachments()  # type:  Attachments

        #self.mint_tokens(token, attachments.receiver_addr, address, tokens, storage)
        from_address = attachments.receiver_addr
        to_address = address

        # bl: the following is an exact copy of the mint_tokens function. invoking self.mint_tokens will break the
        # execution of this method due to a neo-boa compiler issue. this results in a lot of code duplication,
        # but it's preferable to the alternative of a broken smart contract. refer: https://github.com/CityOfZion/neo-boa/issues/40

        # lookup the current balance of the address
        current_balance = storage.get(to_address)

        # add it to the exchanged tokens and persist in storage
        new_total = tokens + current_balance
        storage.put(to_address, new_total)

        # update the in circulation amount
        token.add_to_circulation(tokens, storage)

        # dispatch transfer event
        OnTransfer(from_address, to_address, tokens)

        return True

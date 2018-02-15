from nrve.common.storage import StorageAPI


class Token:
    """
    Basic settings for an NEP5 Token and crowdsale
    """

    name = 'Narrative Token'

    symbol = 'NRVE'

    decimals = 8

    # This is the script hash of the address for the owner of the token
    # This can be found in ``neo-python`` with the wallet open, use ``wallet`` command
    original_owner = b'\xf0\x8a\xb3z\xa8\x88\xa4J\x8a\xba\xb5\x1b\xb2\x92r{\t;\xb1\xdf'

    owner_key = b'owner'
    new_owner_key = b'new_owner'
    sale_paused_key = b'sale_paused'

    in_circulation_key = b'in_circulation'

    #supply_limit = 197500000 * 100000000  # 197.5m total supply * 10^8 (decimals)
    sale_token_limit = 50000000 * 100000000  # 50m tokens for sale * 10^8 (decimals)

    def crowdsale_available_amount(self):
        """

        :return: int The amount of tokens left for sale in the crowdsale
        """
        storage = StorageAPI()

        in_circ = storage.get(self.in_circulation_key)

        available = self.sale_token_limit - in_circ

        if available < 0:
            return 0

        return available

    def add_to_circulation(self, amount: int, storage: StorageAPI):
        """
        Adds an amount of token to circulation

        :param amount: int the amount to add to circulation
        :param storage: StorageAPI A StorageAPI object for storage interaction
        """
        current_supply = storage.get(self.in_circulation_key)

        current_supply += amount

        storage.put(self.in_circulation_key, current_supply)

    def get_circulation(self, storage: StorageAPI):
        """
        Get the total amount of tokens in circulation

        :param storage: StorageAPI A StorageAPI object for storage interaction
        :return:
            int: Total amount in circulation
        """
        return storage.get(self.in_circulation_key)

from nrv.common.storage import StorageAPI


class Token:
    """
    Basic settings for an NEP5 Token and crowdsale
    """

    name = 'Narrative'

    symbol = 'NRV'

    decimals = 8

    # This is the script hash of the address for the owner of the token
    # This can be found in ``neo-python`` with the wallet open, use ``wallet`` command
    original_owner = b'#\xba\'\x03\xc52c\xe8\xd6\xe5"\xdc2 39\xdc\xd8\xee\xe9'

    owner_key = b'owner'
    sale_paused_key = b'sale_paused'

    in_circulation_key = b'in_circulation'

    total_supply = 197500000 * 100000000  # 197.5m total supply * 10^8 (decimals)
    sale_token_limit = 50000000 * 100000000  # 50m tokens for sale * 10^8 (decimals)

    def crowdsale_available_amount(self):
        """

        :return: int The amount of tokens left for sale in the crowdsale
        """
        storage = StorageAPI()

        in_circ = storage.get(self.in_circulation_key)

        available = self.sale_token_limit - in_circ

        return available

    def add_to_circulation(self, amount: int, storage: StorageAPI):
        """
        Adds an amount of token to circlulation

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

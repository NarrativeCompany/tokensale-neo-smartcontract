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
    # neo-privnet.wallet
    #original_owner = b'#\xba\'\x03\xc52c\xe8\xd6\xe5"\xdc2 39\xdc\xd8\xee\xe9'
    # neo-gui.wallet
    original_owner = b'\xde\xf3Z-\x0ci\xfe\xb9\xc4\xcc\x96p\x1f\xd4\xdb\x8fZ\x044\x00'

    owner_key = b'owner'
    new_owner_key = b'new_owner'
    sale_paused_key = b'sale_paused'

    in_circulation_key = b'in_circulation'

    presale_minted_key = b'pre_sale_mint'
    public_sale_sold_key = b'pub_sale_sold'

    # supply_limit = 197500000 * 100000000  # 197.5m total supply * 10^8 (decimals)
    # bl: we sold 20,220,000 tokens in the pre-sale. thus, the public sale token limit is now 29,780,000
    public_sale_token_limit = 29780000 * 100000000  # (50m tokens for sale - 20.22m sold in pre-sale) = 29.78m * 10^8 (decimals)

    def crowdsale_available_amount(self):
        """

        :return: int The amount of tokens left for sale in the crowdsale
        """
        storage = StorageAPI()

        public_sale_sold = storage.get(self.public_sale_sold_key)

        # bl: the total amount of tokens available is now based off of how many tokens have been sold during the public sale
        available = self.public_sale_token_limit - public_sale_sold

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

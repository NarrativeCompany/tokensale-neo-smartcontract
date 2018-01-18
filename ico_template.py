"""
NEX ICO Template
===================================

Author: Thomas Saunders
Email: tom@neonexchange.org

Date: Dec 11 2017

"""

from boa.blockchain.vm.Neo.Runtime import GetTrigger, CheckWitness, Notify
from boa.blockchain.vm.Neo.TriggerType import Application, Verification
from nex.common.storage import StorageAPI
from nex.common.txio import Attachments,get_asset_attachments
from nex.token.mytoken import Token
from nex.token.nep5 import NEP5Handler
from nex.token.crowdsale import Crowdsale


def Main(operation, args):
    """

    :param operation: str The name of the operation to perform
    :param args: list A list of arguments along with the operation
    :return:
        bytearray: The result of the operation
    """

    trigger = GetTrigger()
    token = Token()

    # This is used in the Verification portion of the contract
    # To determine whether a transfer of system assets ( NEO/Gas) involving
    # This contract's address can proceed
    if trigger == Verification:

        storage = StorageAPI()

        # don't allow any contributions if the sale is paused
        if storage.get(token.sale_paused_key):
            return False

        owner = storage.get(token.owner_key)

        # If the invoker is the owner of this contract, proceed
        if CheckWitness(owner):

            return True

        # Otherwise, we need to lookup the assets and determine
        # If attachments of assets is ok
        attachments = get_asset_attachments()  # type:Attachments

        crowdsale = Crowdsale()

        # the exchange will be allowed if the number of tokens to convert to is greater than zero.
        # zero indicates that there is a reason this contribution will not be allowed
        return crowdsale.check_and_calculate_tokens(token, attachments, storage) > 0

    elif trigger == Application:

        if operation is not None:

            nep = NEP5Handler()

            for op in nep.get_methods():
                if operation == op:
                    return nep.handle_nep51(operation, args, token)

            if operation == 'deploy':
                return deploy(token)

            if operation == 'circulation':
                storage = StorageAPI()
                return token.get_circulation(storage)

            # the following are handled by crowdsale

            sale = Crowdsale()

            if operation == 'mintTokens':
                return sale.exchange(token)

            if operation == 'crowdsale_register':
                return sale.kyc_register(args, token)

            if operation == 'crowdsale_deregister':
                return sale.kyc_deregister(args, token)

            if operation == 'crowdsale_status':
                return sale.kyc_status(args)

            if operation == 'crowdsale_available':
                return token.crowdsale_available_amount()

            if operation == 'transfer_team_tokens':
                return sale.transfer_team_tokens(token, args)

            if operation == 'transfer_company_tokens':
                return sale.transfer_company_tokens(token, args)

            if operation == 'mint_rewards_tokens':
                return sale.mint_rewards_tokens(token, args)

            if operation == 'change_owner':
                owner = args[0]
                return change_owner(token, owner)

            if operation == 'pause_sale':
                return pause_sale(token)

            if operation == 'resume_sale':
                return resume_sale(token)

            return 'unknown operation'

    return False


def deploy(token: Token):
    """

    :param token: Token The token to deploy
    :return:
        bool: Whether the operation was successful
    """
    if not CheckWitness(token.original_owner):
        print("Must be original_owner to deploy")
        return False

    storage = StorageAPI()

    if not storage.get(token.owner_key):

        # mark the current owner, which can be changed later
        storage.put(token.owner_key, token.original_owner)

        return True

    return False


def change_owner(token: Token, new_owner):
    """
    Change the owner of this smart contract who will be able to perform protected operations
    :param token: Token The token to change the owner for
    :param new_owner: the new owner of the contract
    :return:
        bool: Whether the operation was successful
    """
    storage = StorageAPI()

    owner = storage.get(token.owner_key)
    if not owner:
        print("Must deploy before changing owner")
        return False

    if not CheckWitness(owner):
        print("Must be owner to change owner")
        return False

    # set the new owner
    storage.put(token.owner_key, new_owner)

    return True


def pause_sale(token: Token):
    storage = StorageAPI()

    # mark the sale as paused
    storage.put(token.sale_paused_key, True)

    return True


def resume_sale(token: Token):
    storage = StorageAPI()

    # mark the sale as active
    storage.delete(token.sale_paused_key)

    return True

from boa.blockchain.vm.Neo.Blockchain import GetHeight,GetHeader


def get_now():
    height = GetHeight()
    current_block = GetHeader(height)
    return current_block.Timestamp

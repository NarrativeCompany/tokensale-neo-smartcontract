# Narrative NRVE Smart Contract
Narrative's NEO Token Sale Smart Contract for NEP-5 NRVE Token Distribution

Based originally off of NEX's NEO ICO Template: https://github.com/neonexchange/neo-ico-template

### Python Smart Contract

`ico_template.py` is the smart contract entry point. The smart contract supports:

* NEP-5 NRVE token
* Token sale (ICO) distribution mechanism.
* 50m NRVE tokens for sale.
* Unsold tokens are burned.
* Private pre-sale phase with 800 NEO minimum and 3,000 NEO maximum contributions with NRVE distributed 400:1 per NEO.
  * The original smart contract ([nrv-1.0](https://github.com/NarrativeNetwork/tokensale-neo-smartcontract/tree/nrv-1.0))
  support contributions during this phase.
  * The new smart contract ([nrve-1.1](https://github.com/NarrativeNetwork/tokensale-neo-smartcontract/tree/nrve-1.1))
  supports manual disbursements of the 20.22m tokens sold during the life of the original
  smart contract (before failure).
* Day 1 contribution maximum of 300 NEO with NRVE distributed 333:1 per NEO.
* Day 2 contribution maximum of 1,000 NEO with NRVE distributed 315:1 per NEO.
* Day 3+ contributions (no maximum) with NRVE distributed 300:1 per NEO.
* 30m Company Token distribution on 2 year vesting schedule starting March 29, 2018 (immediately post-sale).
* 20m Team Token distribution on 3 year vesting schedule starting January 2019.
* 97.5m Network Rewards Token distribution through manual minting.
* KYC whitelist registration and deregistration of addresses in bulk.
* Pausing and resuming the sale.
* Transferring ownership via "two-phase commit" to ensure new owner has proper access.
* Compile with neo-boa 0.2.1 to avoid 0.2.2 issues

### Smart Contract Event Handler

`util/neo-nrve-eventhandler.py` is a [neo-python](https://github.com/CityOfZion/neo-python) blockchain node
implementation for listening to incoming smart contract events. It supports:

* Processing KYC whitelist registration and deregistration via direct SQL updates.
* Recording refunds via direct SQL inserts and notification emails since the refunds must be processed manually.
* Recording token sale contributions via direct SQL inserts, triggered by a custom `contribution` (non-NEP-5) event.
* Ignoring NEP-5 `transfer` and `approve` events.
* Support for recording refunds the "old" smart contract.
* Error handling and reporting with any of the above events.

Run the script from a neo-python installation from the neo/contrib directory.

### Bulk Transaction Processor

`util/bulk-process-tx.py` is a handy utility for processing transactions in bulk without needing to enter
your password for every transaction. We had to whitelist over 7,000 addresses for our token sale. In order
to stay within the 10 free GAS limit, we could safely whitelist 6 addresses per transaction. This script
allowed us to automate that process.

It supports:

* Waiting for the blockchain to sync initially.
* Loading the wallet password once and re-using as appropriately throughout the process.
* Wallet rebuilds when it gets out of sync ("insufficient funds").
  * This requires a backup, synchronized wallet to reload from with the ".syncd" extension added to the filename
  in order to prevent the need to do a full wallet rebuild, which can be very slow.
* Configs to easily run against privnet, testnet, or mainnet.
* Easy configs to define the method and parameters.
  * Parameters are raw strings so that they are parsed exactly as through `prompt.py`.
* Extra validation to support verifying the testinvoke result has the correct Integer value. If this fails
then the tx invoke will not be relayed.
* Special "test mode" to do the testinvoke only without relaying transactions.
* Processes transactions sequentially and will only submit the next transaction once the current
  transaction has been successfully executed.
  * The one minor hole currently is when a transaction isn't accepted by the blockchain, in which case
  the script will hang and wait forever for the transaction to complete.
* The script assumes the smart contract has one string and one array argument.
* When running, it is recommended to:
  1) Update `Wallet.ProcessBlocks()` so that instead of only building at most 500 blocks at a time, the method
  will block until the full wallet is rebuilt and ready to be used (i.e. no max of 500).
  2) Edit `neo/Prompt/Commands/Invoke.py` to set the `net_fee` to a smaller value to reduce GAS fees, e.g.:
```
net_fee = Fixed8.FromDecimal(.0004)
```
* It could be improved to take the current script params as arguments instead of being baked into the code.

Run the script from a neo-python installation from the neo/contrib directory.
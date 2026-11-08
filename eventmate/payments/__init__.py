"""Deposits and balances: what was agreed, what was approved, what was paid. The app records and instructs; it never holds or moves a user's money by itself.

* ``terms``: the money terms of a vendor's confirmed quote (deposit, balance, due dates, payee), proposed from the reply and confirmed by the user.
* ``approvals``: a payment is proposed, approved by the user against a hash of its exact amount, currency, payee and quote, and expires; every step and every attempt is logged.
* ``providers``: how an approved payment is carried out. The default only tells the user exactly what to pay and how, in words; a PayPal sandbox adapter exists behind a flag that is off.

The app does not guarantee that a vendor delivers anything. A quote is not a booking and a payment instruction is not a payment.
"""

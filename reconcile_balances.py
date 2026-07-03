#!/usr/bin/env python3
"""
Standalone utility script to fix corrupted customer balances caused by old app versions.
It recalculates each customer's balance to match their exact unpaid invoices (-total_unpaid).
If a customer has 0 pending payments, their balance is reset to 0.00.
"""

from app import app, db, Customer, Payment
from sqlalchemy import func
import sys

if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

def fix_all_customer_balances():
    with app.app_context():
        customers = Customer.query.all()
        fixed_count = 0
        for c in customers:
            unpaid_total = db.session.query(func.coalesce(func.sum(Payment.amount), 0.0)).filter_by(
                customer_id=c.id, paid=False
            ).scalar()
            
            correct_balance = -float(unpaid_total)
            if abs(float(c.balance) - correct_balance) > 0.001:
                safe_name = c.name.encode('utf-8', 'replace').decode('utf-8')
                print(f"[FIX] Customer ID {c.id} ({safe_name}): Old Balance = {c.balance:.2f} -> New Correct Balance = {correct_balance:.2f}")
                c.balance = correct_balance
                fixed_count += 1
        
        db.session.commit()
        print(f"\n[SUCCESS] Reconciled and fixed balances for {fixed_count} customer(s). Total checked: {len(customers)}.")

if __name__ == '__main__':
    fix_all_customer_balances()

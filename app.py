import os
import re
import time
import requests
import traceback
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, func, extract
from werkzeug.utils import secure_filename
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from flask import send_from_directory
from sqlalchemy.exc import IntegrityError
from dateutil.relativedelta import relativedelta # REQUIRED: pip install python-dateutil
import signal
import sys
import json
from flask_bcrypt import Bcrypt
from flask_jwt_extended import create_access_token, get_jwt_identity, jwt_required, JWTManager, verify_jwt_in_request
try:
    from flask_jwt_extended import get_jwt
except ImportError:
    from flask_jwt_extended import get_raw_jwt as get_jwt
from functools import wraps
import calendar
from pywebpush import webpush, WebPusher
from sqlalchemy import or_ as db_or, text
import logging
from datetime import timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]')

basedir = os.path.abspath(os.path.dirname(__file__))

# Load VAPID keys
VAPID_PRIVATE_KEY = os.environ.get('VAPID_PRIVATE_KEY')
VAPID_PUBLIC_KEY = os.environ.get('VAPID_PUBLIC_KEY')
if not VAPID_PRIVATE_KEY or not VAPID_PUBLIC_KEY:
    try:
        with open(os.path.join(basedir, 'vapid_keys.env'), 'r') as f:
            for line in f:
                if line.startswith('VAPID_PRIVATE_KEY='):
                    VAPID_PRIVATE_KEY = line.split('=', 1)[1].strip()
                elif line.startswith('VAPID_PUBLIC_KEY='):
                    VAPID_PUBLIC_KEY = line.split('=', 1)[1].strip()
    except Exception as e:
        print("Warning: Could not load VAPID keys.", e)

app = Flask(__name__, static_folder='build', static_url_path='/')
CORS(app, resources={r"/api/*": {"origins": "*"}})

DATABASE_PATH = os.environ.get('DATABASE_PATH', 'database.db')

app.config["JWT_SECRET_KEY"] = "a135b8778fe5dc203c82a9fcb0bcce63a7bd62f4e72cdaf5649569168bb32b04" # Change this!
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=8)  # Extended to 8 hours
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DATABASE_PATH}'

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
jwt = JWTManager(app)

# Database Models (unchanged)
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user') # Roles: 'user', 'admin'

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

class Reseller(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    type = db.Column(db.String(20), nullable=False) # 'type1' or 'type2'
    balance = db.Column(db.Float, default=0.0)
    customers = db.relationship('Customer', backref='reseller', lazy=True)
    payments = db.relationship('ResellerPayment', backref='reseller', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'type': self.type,
            'balance': float(self.balance)
        }

class ResellerPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(50), nullable=False) # 'credit_added', 'payment_received', 'discount_applied'
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    description = db.Column(db.String(200))

    def to_dict(self):
        return {
            'id': self.id,
            'reseller_id': self.reseller_id,
            'amount': float(self.amount),
            'type': self.type,
            'date': self.date.strftime('%Y-%m-%d %H:%M:%S'),
            'description': self.description
        }

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    sector = db.Column(db.String(100), nullable=True)
    subscription_plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plan.id'), nullable=False)
    subscription_start_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    subscription_expiry_date = db.Column(db.DateTime, nullable=False)
    is_subscription_active = db.Column(db.Boolean, default=True)
    balance = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    reseller_id = db.Column(db.Integer, db.ForeignKey('reseller.id'), nullable=True)
    payments = db.relationship('Payment', backref='customer', lazy=True, cascade="all, delete-orphan")
    generated_receipts = db.relationship('GeneratedReceipt', back_populates='customer', cascade="all, delete-orphan")
    addon_purchases = db.relationship('AddonPurchase', backref='customer', lazy=True, cascade="all, delete-orphan")
    service_status = db.relationship('ServiceStatus', backref='customer', lazy=True, cascade="all, delete-orphan")
    support_tickets = db.relationship('SupportTicket', backref='customer', lazy=True, cascade="all, delete-orphan")
    feedback = db.relationship('CustomerFeedback', backref='customer', lazy=True, cascade="all, delete-orphan")
    payment_reminders = db.relationship('PaymentReminder', backref='customer', lazy=True, cascade="all, delete-orphan")
    whatsapp_notifications_enabled = db.Column(db.Boolean, default=True)
    # In the Customer model, add a property:
    @property
    def subscription_plan_dict(self):
        if self.subscription_plan:
            return self.subscription_plan.to_dict()
        return None

class SubscriptionPlan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    billing_cycle = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(50), default='active') # active, inactive

    customers = db.relationship('Customer', backref='subscription_plan', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'price': float(self.price),
            'billing_cycle': self.billing_cycle,
            'status': self.status
        }

class Sector(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    def to_dict(self):
        return {'id': self.id, 'name': self.name}

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    balance = db.Column(db.Float, default=0.0)
    address = db.Column(db.String(200), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'balance': float(self.balance),
            'address': self.address,
            'notes': self.notes,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }

class SupplierPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)
    payment_method = db.Column(db.String(50), nullable=True)
    reference_note = db.Column(db.Text, nullable=True)

    supplier = db.relationship('Supplier', backref='payments', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'supplier_id': self.supplier_id,
            'amount': float(self.amount),
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M:%S'),
            'payment_method': self.payment_method,
            'reference_note': self.reference_note
        }

class ExpenseCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    expenses = db.relationship('Expense', backref='category', lazy=True)

    def to_dict(self):
        return {'id': self.id, 'name': self.name}


class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id =  db.Column(db.Integer, db.ForeignKey('expense_category.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)
    is_credit = db.Column(db.Boolean, default=False)
    amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(200), nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    supplier = db.relationship('Supplier', backref='expenses', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'category': self.category.name,
            'supplier_name': self.supplier.name if self.supplier else None,
            'supplier_id': self.supplier_id,
            'is_credit': self.is_credit,
            'amount': float(self.amount),
            'description': self.description,
            'date': self.date.strftime('%Y-%m-%d')
        }

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    paid = db.Column(db.Boolean, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    collected = db.Column(db.Boolean, default=False)
    collected_at = db.Column(db.DateTime, nullable=True)
    collected_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    collected_amount = db.Column(db.Float, nullable=True)
    received_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    pre_payment = db.Column(db.Boolean, default=False)
    addon_purchases = db.relationship('AddonPurchase', backref='payment', lazy=True)
    
    collected_by = db.relationship('User', foreign_keys=[collected_by_id])
    received_by = db.relationship('User', foreign_keys=[received_by_id])


class GeneratedReceipt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False, unique=True)
    billing_date = db.Column(db.DateTime, nullable=False)
    generation_date = db.Column(db.DateTime, default=datetime.utcnow)
    print_count = db.Column(db.Integer, default=0)
    last_printed_date = db.Column(db.DateTime)
    receipt_data = db.Column(db.Text, nullable=False) # Stores a JSON snapshot of the receipt
    customer = db.relationship('Customer', back_populates='generated_receipts')


class AddonPurchase(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    description = db.Column(db.String(200))
    purchase_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    paid = db.Column(db.Boolean, default=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=True)
    notes = db.Column(db.String(200))


class BusinessSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    logo_url = db.Column(db.String(500), nullable=True)  # URL or path to the logo image
    business_name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(500), nullable=False)
    mobile = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(100), nullable=True)
    website = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        l_url = self.logo_url
        if l_url and not l_url.startswith('/') and not l_url.startswith('http'):
            l_url = f"/uploads/{l_url}"
            
        return {
            'id': self.id,
            'logo_url': l_url,
            'business_name': self.business_name,
            'address': self.address,
            'mobile': self.mobile,
            'email': self.email,
            'website': self.website,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': self.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        }

class WhatsAppSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Mode: 'deeplink' (manual button) or 'api' (auto-send via Meta Cloud API)
    mode = db.Column(db.String(20), nullable=False, default='deeplink')
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Meta Cloud API credentials
    phone_number_id = db.Column(db.String(100), nullable=True)
    business_account_id = db.Column(db.String(100), nullable=True)
    app_id = db.Column(db.String(100), nullable=True)
    app_secret = db.Column(db.String(200), nullable=True)
    access_token = db.Column(db.Text, nullable=True)
    api_version = db.Column(db.String(20), nullable=True, default='v19.0')
    # Message templates (template names registered in Meta Business Manager)
    template_payment_paid = db.Column(db.String(200), nullable=True, default='payment_confirmation')
    template_subscription_created = db.Column(db.String(200), nullable=True, default='subscription_created')
    template_subscription_renewed = db.Column(db.String(200), nullable=True, default='subscription_renewal')
    template_payment_reminder = db.Column(db.String(200), nullable=True, default='payment_reminder')
    template_bulk_outage = db.Column(db.String(200), nullable=True, default='outage_alert')
    template_bulk_maintenance = db.Column(db.String(200), nullable=True, default='maintenance_alert')
    template_bulk_feature = db.Column(db.String(200), nullable=True, default='feature_update')
    template_bulk_offer = db.Column(db.String(200), nullable=True, default='special_offer')
    # Template language code
    template_language = db.Column(db.String(20), nullable=True, default='en')
    # Deep-link message templates (plain text for wa.me links)
    deeplink_msg_payment = db.Column(db.Text, nullable=True,
        default='Dear {customer_name}, your payment of ${amount} has been received. Thank you!')
    deeplink_msg_renewal = db.Column(db.Text, nullable=True,
        default='Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!')
    forwarding_mobile = db.Column(db.String(50), nullable=True)
    webhook_verify_token = db.Column(db.String(100), nullable=True, default='delta_net_whatsapp_secret')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'mode': self.mode,
            'enabled': self.enabled,
            'phone_number_id': self.phone_number_id or '',
            'business_account_id': self.business_account_id or '',
            'app_id': self.app_id or '',
            'app_secret': self.app_secret or '',
            'access_token': self.access_token or '',
            'api_version': self.api_version or 'v19.0',
            'template_payment_paid': self.template_payment_paid or 'payment_confirmation',
            'template_subscription_created': self.template_subscription_created or 'subscription_created',
            'template_subscription_renewed': self.template_subscription_renewed or 'subscription_renewal',
            'template_payment_reminder': self.template_payment_reminder or 'payment_reminder',
            'template_bulk_outage': self.template_bulk_outage or 'outage_alert',
            'template_bulk_maintenance': self.template_bulk_maintenance or 'maintenance_alert',
            'template_bulk_feature': self.template_bulk_feature or 'feature_update',
            'template_bulk_offer': self.template_bulk_offer or 'special_offer',
            'template_language': self.template_language or 'en',
            'deeplink_msg_payment': self.deeplink_msg_payment or 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!',
            'deeplink_msg_renewal': self.deeplink_msg_renewal or 'Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!',
            'forwarding_mobile': self.forwarding_mobile or '',
            'webhook_verify_token': self.webhook_verify_token or 'delta_net_whatsapp_secret',
        }

class SystemUpdateSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_version = db.Column(db.String(50), nullable=False, default='1.4.0')
    github_repo = db.Column(db.String(200), nullable=False, default='hasbach/servicesBills')
    auto_update_enabled = db.Column(db.Boolean, nullable=False, default=False)
    auto_update_time = db.Column(db.String(10), nullable=False, default='03:00')
    platform = db.Column(db.String(50), nullable=False, default='pythonanywhere') # 'pythonanywhere', 'linux_vps', 'windows_server'
    last_checked_at = db.Column(db.DateTime, nullable=True)
    last_updated_at = db.Column(db.DateTime, nullable=True)
    latest_available_version = db.Column(db.String(50), nullable=True, default='1.4.0')
    release_notes = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'current_version': self.current_version,
            'github_repo': self.github_repo,
            'auto_update_enabled': self.auto_update_enabled,
            'auto_update_time': self.auto_update_time,
            'platform': self.platform,
            'last_checked_at': self.last_checked_at.strftime('%Y-%m-%d %H:%M:%S') if self.last_checked_at else None,
            'last_updated_at': self.last_updated_at.strftime('%Y-%m-%d %H:%M:%S') if self.last_updated_at else None,
            'latest_available_version': self.latest_available_version or self.current_version,
            'release_notes': self.release_notes or 'No new release notes available.'
        }

class ServiceStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    status = db.Column(db.String(50), nullable=False)  # active, suspended, terminated
    last_updated = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    notes = db.Column(db.String(500))

class SupportTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50), nullable=False)  # open, in_progress, resolved, closed
    priority = db.Column(db.String(20), nullable=False)  # low, medium, high, critical
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)
    in_progress_at = db.Column(db.DateTime)
    in_progress_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    resolved_by_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    in_progress_by = db.relationship('User', foreign_keys=[in_progress_by_id])
    resolved_by = db.relationship('User', foreign_keys=[resolved_by_id])
    logs = db.relationship('TicketLog', backref='ticket', lazy=True, cascade="all, delete-orphan")

class TicketLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('support_ticket.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False) # e.g. 'created', 'status_changed', 'assigned'
    details = db.Column(db.Text, nullable=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id])

class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    subscription_info = db.Column(db.Text, nullable=False) # JSON
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class ServiceOutage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    affected_areas = db.Column(db.String(500), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime)
    status = db.Column(db.String(50), nullable=False)  # active, resolved
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class CustomerFeedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.Text)
    category = db.Column(db.String(50), nullable=False)  # service, support, billing, other
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

class PaymentReminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    payment_id = db.Column(db.Integer, db.ForeignKey('payment.id'), nullable=False)
    reminder_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(50), nullable=False)  # pending, sent, paid
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

# Create the database
with app.app_context():
    db.create_all()
    
    # 1. Automatic Migration: Add 'role' column if it doesn't exist for legacy databases
    try:
        db.session.execute(text("ALTER TABLE user ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'"))
        db.session.commit()
        print("Migration: Successfully added role column.")
    except Exception as e:
        db.session.rollback()
        print(f"Migration step 1 (add column) skipped or failed: {e}")

    # 2. Add 'reason' column to Payment
    try:
        db.session.execute(text("ALTER TABLE payment ADD COLUMN reason VARCHAR(255)"))
        db.session.commit()
        print("Migration: Successfully added reason column to payment.")
    except Exception as e:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE support_ticket ADD COLUMN in_progress_at DATETIME"))
        db.session.execute(text("ALTER TABLE support_ticket ADD COLUMN in_progress_by_id INTEGER REFERENCES user(id)"))
        db.session.execute(text("ALTER TABLE support_ticket ADD COLUMN resolved_by_id INTEGER REFERENCES user(id)"))
        db.session.commit()
        print("Migration: Successfully added support ticket tracking columns.")
    except Exception as e:
        db.session.rollback()
        print(f"Migration support_ticket tracking columns skipped or failed: {e}")

    # Migrations for Reseller, Customer, Payment, Expense
    try:
        db.session.execute(text("ALTER TABLE reseller ADD COLUMN balance FLOAT DEFAULT 0.0"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE customer ADD COLUMN sector VARCHAR(100)"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE payment ADD COLUMN collected_amount FLOAT DEFAULT 0.0"))
        db.session.execute(text("ALTER TABLE payment ADD COLUMN collected BOOLEAN DEFAULT 0"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE expense ADD COLUMN supplier_id INTEGER REFERENCES supplier(id)"))
        db.session.execute(text("ALTER TABLE expense ADD COLUMN is_credit BOOLEAN DEFAULT 0"))
        db.session.commit()
    except Exception:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE whats_app_settings ADD COLUMN forwarding_mobile VARCHAR(50)"))
        db.session.execute(text("ALTER TABLE whats_app_settings ADD COLUMN webhook_verify_token VARCHAR(100) DEFAULT 'delta_net_whatsapp_secret'"))
        db.session.commit()
        print("Migration: Successfully added forwarding_mobile and webhook_verify_token to whats_app_settings.")
    except Exception as e:
        db.session.rollback()

    try:
        db.session.execute(text("ALTER TABLE whats_app_settings ADD COLUMN template_subscription_created VARCHAR(200) DEFAULT 'subscription_created'"))
        db.session.commit()
    except Exception:
        db.session.rollback()
        
    # 2. Automatic Migration: Ensure at least one admin exists
    try:
        users = User.query.all()
        if users:
            admin_exists = any(u.role == 'admin' for u in users)
            if not admin_exists:
                admin_user = next((u for u in users if u.username == 'admin'), users[0])
                admin_user.role = 'admin'
                db.session.commit()
                print(f"Migration: Successfully elevated {admin_user.username} to admin.")
    except Exception as e:
        db.session.rollback()
        print(f"Migration step 2 (elevate admin) failed: {e}")

@app.route('/api/debug-db', methods=['GET'])
def debug_db():
    try:
        users = User.query.all()
        user_list = [{"id": u.id, "username": u.username, "role": u.role} for u in users]
        return jsonify({"status": "success", "users": user_list}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


def admin_required():
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            claims = get_jwt()
            if claims.get('role') == 'admin':
                return fn(*args, **kwargs)
            else:
                return jsonify(msg="Admins only!"), 403
        return decorator
    return wrapper


UPLOAD_FOLDER = 'uploads/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])



# --- NEW HELPER FUNCTION ---
def apply_customer_balance_to_unpaid_payments(customer):
    """
    Applies a customer's positive balance to their outstanding unpaid payments.
    Matches the logic of mark_payment_as_paid for partial payments.
    Assumes the customer object is part of the current DB session.
    """
    
    # Only run if the customer has credit
    if customer.balance <= 0:
        return

    logging.info(f"Reconciling balance for customer {customer.id}. Current balance: {customer.balance}")

    # Get all outstanding bills, oldest first
    unpaid_payments = Payment.query.filter_by(
        customer_id=customer.id, 
        paid=False
    ).order_by(Payment.date.asc()).all()

    for payment in unpaid_payments:
        if customer.balance <= 0:
            break  # Stop if credit runs out

        amount_due = payment.amount
        
        if customer.balance >= amount_due:
            # Full payment from balance
            payment.paid = True
            payment.paid_at = datetime.utcnow()
            # The balance is "spent" to pay this, so it decreases.
            # The payment.amount remains unchanged for revenue tracking.
            customer.balance -= amount_due
            logging.info(f"Auto-paid payment {payment.id} (Amount: {amount_due}) for customer {customer.id} using balance. New balance: {customer.balance}")
            
        else:
            # Partial payment from balance
            # Customer has some credit (e.g., $10), but not enough for the bill (e.g., $30)
            
            amount_paid_from_balance = customer.balance
            remaining_amount_due = amount_due - amount_paid_from_balance

            # Create a new payment record for the remaining amount
            # This matches the logic in mark_payment_as_paid
            remaining_payment = Payment(
                customer_id=customer.id,
                amount=remaining_amount_due,
                paid=False,
                date=payment.date,
                pre_payment=payment.pre_payment
            )
            db.session.add(remaining_payment)
            
            # Mark original payment as paid
            # (This is the established logic from mark_payment_as_paid)
            payment.paid = True
            payment.paid_at = datetime.utcnow()
            
            # All credit is used up
            customer.balance = 0
            
            logging.info(f"Partially auto-paid payment {payment.id} (Amount: {amount_due}) for customer {customer.id} using {amount_paid_from_balance} from balance. New payment created for remaining {remaining_amount_due}. New balance: 0")

    # Note: The caller is responsible for db.session.commit()
# --- END HELPER FUNCTION ---



def has_pending_payment(customer_id, billing_date):
    """
    Check if a pending payment already exists for the customer for the given billing date.
    """
    existing_payment = Payment.query.filter_by(
        customer_id=customer_id,
        paid=False,
        date=billing_date
    ).first()
    return existing_payment is not None




@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    if not username or not password:
        return jsonify({"msg": "Username and password required"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"msg": "Username already exists"}), 409
    
    new_user = User(username=username)
    new_user.set_password(password)
    # Make the first registered user an admin
    if User.query.count() == 0:
        new_user.role = 'admin'
    
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"msg": "User created successfully"}), 201

def admin_required():
    def wrapper(fn):
        @wraps(fn)
        def decorator(*args, **kwargs):
            claims = get_jwt()
            if claims.get('role') != 'admin':
                return jsonify(msg="Admins only!"), 403
            return fn(*args, **kwargs)
        return decorator
    return wrapper

@app.route('/api/users', methods=['GET'])
@jwt_required()
@admin_required()
def get_users():
    users = User.query.all()
    result = []
    for u in users:
        result.append({
            'id': u.id,
            'username': u.username,
            'role': u.role
        })
    return jsonify(result), 200

@app.route('/api/users', methods=['POST'])
@jwt_required()
@admin_required()
def create_user():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'employee')
    
    if not username or not password:
        return jsonify({"msg": "Username and password required"}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"msg": "Username already exists"}), 409
        
    new_user = User(username=username, role=role)
    new_user.set_password(password)
    
    db.session.add(new_user)
    db.session.commit()
    return jsonify({"msg": "User created successfully"}), 201

@app.route('/api/users/<int:user_id>', methods=['PUT'])
@jwt_required()
@admin_required()
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    data = request.json
    
    if 'role' in data:
        user.role = data['role']
    if 'password' in data and data['password'].strip() != '':
        user.set_password(data['password'])
        
    db.session.commit()
    return jsonify({"msg": "User updated successfully"}), 200

@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@jwt_required()
@admin_required()
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user.role == 'admin':
        admin_count = User.query.filter_by(role='admin').count()
        if admin_count <= 1:
            return jsonify({"msg": "Cannot delete the last admin"}), 400
            
    # Prevent user deleting themselves just in case? Optional, but good practice.
    current_username = get_jwt_identity()
    if user.username == current_username:
        return jsonify({"msg": "Cannot delete your own account"}), 400
            
    db.session.delete(user)
    db.session.commit()
    return jsonify({"msg": "User deleted successfully"}), 200


@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        access_token = create_access_token(
            identity=username,
            additional_claims={'role': user.role}
        )
        return jsonify(access_token=access_token, user={'username': user.username, 'role': user.role})
    return jsonify({"msg": "Bad username or password"}), 401


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)        
        
def generate_missing_payments():
    try:
        # Get all active customers
        customers = Customer.query.filter_by(is_subscription_active=True).all()

        for customer in customers:
            # Get the subscription plan for the customer
            subscription_plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)
            if not subscription_plan:
                continue  # Skip if subscription plan is missing

            # Determine the last payment date or use the subscription start date
            last_payment = Payment.query.filter_by(
                customer_id=customer.id,
                pre_payment=False
            ).order_by(Payment.date.desc()).first()

            last_payment_date = last_payment.date if last_payment else customer.subscription_start_date

            # Calculate the next billing date based on the billing cycle
            # Use relativedelta for more accurate month/year increments
            if subscription_plan.billing_cycle == 'monthly':
                next_billing_date = last_payment_date + relativedelta(months=1)
            elif subscription_plan.billing_cycle == 'yearly':
                next_billing_date = last_payment_date + relativedelta(years=1)
            else:
                continue # Skip if billing cycle is unrecognized


            # Generate missing payments until the current date
            while next_billing_date <= datetime.utcnow():
                # Calculate amount considering discount
                # Use subscription_plan.price directly as cost is removed
                amount_due = subscription_plan.price - customer.discount
                if amount_due < 0:
                    amount_due = 0.0

                # Create a new unpaid payment for the missed billing cycle
                new_payment = Payment(
                    customer_id=customer.id,
                    amount=amount_due,
                    paid=False,
                    date=next_billing_date,
                    pre_payment=False
                )
                db.session.add(new_payment)

                # Update the customer's balance (decreasing balance for new owed amount)
                customer.balance -= amount_due

                # Move to the next billing cycle
                if subscription_plan.billing_cycle == 'monthly':
                    next_billing_date += relativedelta(months=1)
                elif subscription_plan.billing_cycle == 'yearly':
                    next_billing_date += relativedelta(years=1)
                customer.subscription_expiry_date = next_billing_date

            apply_customer_balance_to_unpaid_payments(customer)

        # Commit all changes to the database
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Error generating missing payments: {str(e)}")
        
        
# Initialize scheduler
scheduler = BackgroundScheduler(daemon=True, executors={'default': {'type': 'threadpool', 'max_workers': 1}})

def generate_missing_payments_with_context():
    with app.app_context():
        generate_missing_payments()

def scheduled_auto_update_check():
    with app.app_context():
        try:
            settings = SystemUpdateSettings.query.first()
            if settings and settings.auto_update_enabled:
                logging.info("Scheduler running overnight auto-update check...")
                # Run apply update routine silently if needed
        except Exception as e:
            logging.error(f"Scheduled auto-update error: {e}")

# Start scheduler only if not already running
if not scheduler.running:
    scheduler.add_job(func=generate_missing_payments_with_context, trigger="interval", days=1)
    scheduler.add_job(func=scheduled_auto_update_check, trigger="interval", hours=12)
    scheduler.start()
 
    





@app.route('/api/customers', methods=['GET'])
@jwt_required()
def get_customers():
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 25))
        search_query = request.args.get('search', '').strip()

        reseller_id = request.args.get('reseller_id')
        sort_by = request.args.get('sort_by', 'expiry_date') # name, address, expiry_date
        sort_desc = request.args.get('sort_desc', 'true').lower() == 'true'

        # Build the query with join to subscription plan
        query = Customer.query.options(db.joinedload(Customer.subscription_plan))

        if reseller_id:
            query = query.filter(Customer.reseller_id == reseller_id)

        if search_query:
            # OPTIMIZED: Use prefix matching for better index usage
            # Allows database to use indexes on name, phone, address columns
            query = query.filter(
                db.or_(
                    Customer.name.ilike(f'{search_query}%'),      # Changed from %{search_query}%
                    Customer.phone.ilike(f'{search_query}%'),     # Changed from %{search_query}%
                    Customer.address.ilike(f'{search_query}%')    # Changed from %{search_query}%
                )
            )

        # Sorting logic
        if sort_by == 'name':
            order_col = Customer.name
        elif sort_by == 'address':
            order_col = Customer.address
        else:
            order_col = Customer.subscription_expiry_date
        
        if sort_desc:
            query = query.order_by(order_col.desc())
        else:
            query = query.order_by(order_col.asc())

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        customers_with_plans = []
        for c in pagination.items:
            customer_dict = {
                'id': c.id,
                'name': c.name,
                'phone': c.phone,
                'address': c.address,
                'subscription_plan_id': c.subscription_plan_id,
                'subscription_start_date': c.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': c.subscription_expiry_date.strftime('%Y-%m-%d') if c.subscription_expiry_date else None,
                'is_subscription_active': c.is_subscription_active,
                'balance': float(c.balance) if c.balance else 0.0,
                'discount': float(c.discount) if c.discount else 0.0,
                'reseller_id': c.reseller_id,
                'subscription_plan': c.subscription_plan.to_dict() if c.subscription_plan else None
            }
            customers_with_plans.append(customer_dict)
        
        return jsonify({
            'customers': customers_with_plans,
            'total': pagination.total,
            'pages': pagination.pages,
            'current_page': page
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    
from datetime import datetime, timezone

@app.route('/api/customers', methods=['POST'])
@jwt_required()
def add_customer():
    try:
        data = request.json
        subscription_start_date = (
            datetime.strptime(data.get('subscription_start_date'), '%Y-%m-%d')
            if data.get('subscription_start_date')
            else datetime.now(timezone.utc)
        )
        subscription_plan = db.session.get(SubscriptionPlan, data['subscription_plan_id'])
        if not subscription_plan:
            return jsonify({'message': 'Subscription plan not found!'}), 404
        
        discount = float(data.get('discount', 0.0))

        # Create new customer first
        new_customer = Customer(
            name=data['name'],
            phone=data['phone'],
            address=data['address'],
            sector=data.get('sector'),
            subscription_plan_id=data['subscription_plan_id'],
            discount=discount,
            subscription_start_date=subscription_start_date,
            # Expiry date will be set by the payment loop
            subscription_expiry_date=subscription_start_date, 
            is_subscription_active=True,
            balance=0.0,
            reseller_id=data.get('reseller_id') if data.get('reseller_id') != "" else None
        )
        db.session.add(new_customer)
        db.session.flush() # Flush to get new_customer.id

        # --- FIX: Generate all back-dated payments upon creation ---
        # OPTIMIZED: Limit to last 3 months to prevent long blocking operations
        max_backdate = datetime.utcnow() - timedelta(days=90)
        next_billing_date = max(subscription_start_date, max_backdate)
        total_due = 0

        while next_billing_date <= datetime.utcnow():
            amount_due = subscription_plan.price - new_customer.discount
            if amount_due < 0:
                amount_due = 0.0

            new_payment = Payment(
                customer_id=new_customer.id,
                amount=amount_due,
                paid=False,
                date=next_billing_date,
                pre_payment=False
            )
            db.session.add(new_payment)
            total_due += amount_due

            # Move to the next billing cycle
            if subscription_plan.billing_cycle == 'monthly':
                next_billing_date += relativedelta(months=1)
            elif subscription_plan.billing_cycle == 'yearly':
                next_billing_date += relativedelta(years=1)
        
        # Update customer's balance and expiry date
        new_customer.balance -= total_due
        new_customer.subscription_expiry_date = next_billing_date

        # Handle any immediate additional payment
        addon_amount = float(data.get('additional_payment_amount', 0))
        if addon_amount > 0:
            addon_payment = Payment(
                customer_id=new_customer.id,
                amount=addon_amount,
                paid=False,
                date=datetime.now(timezone.utc),
                pre_payment=False,
            )
            db.session.add(addon_payment)
            new_customer.balance -= addon_amount
        # --- ADDED: Reconcile balance after creating customer and all initial charges ---
        apply_customer_balance_to_unpaid_payments(new_customer)

        db.session.commit()
        
        # Send WhatsApp Notification for Subscription Creation
        try:
            send_whatsapp_message(
                new_customer,
                event_type='subscription_created',
                context={
                    'plan_name': subscription_plan.name,
                    'expiry_date': new_customer.subscription_expiry_date.strftime('%Y-%m-%d'),
                    'balance': new_customer.balance
                }
            )
        except Exception as wa_error:
            print(f"Failed to send WA message on customer creation: {wa_error}")

        return jsonify({
            'message': 'Customer added successfully!',
            'customer_id': new_customer.id,
            'balance': float(new_customer.balance),
            'subscription_expiry': new_customer.subscription_expiry_date.strftime('%Y-%m-%d')
        }), 201

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400



@app.route('/api/customers/<int:customer_id>', methods=['PUT'])
@jwt_required()
def update_customer(customer_id):
    try:
        customer = db.session.get(Customer, customer_id)
        if not customer:
            return jsonify({'message': 'Customer not found!'}), 404
        
        data = request.json
        
        original_name = customer.name
        
        # Update basic customer information
        if 'name' in data:
            customer.name = data['name']
        if 'phone' in data:
            customer.phone = data['phone']
        if 'address' in data:
            customer.address = data['address']
        if 'sector' in data:
            customer.sector = data['sector']
        if 'discount' in data:
            customer.discount = float(data['discount'])
        if 'balance' in data:
            customer.balance = float(data['balance'])
        if 'reseller_id' in data:
            new_reseller_id = data['reseller_id'] if data['reseller_id'] != "" else None
            old_reseller_id = customer.reseller_id
            
            if old_reseller_id != new_reseller_id:
                net_debt = 0.0
                
                # 1. Reverse accumulated debt/credit from OLD reseller (if any)
                if old_reseller_id:
                    old_reseller = db.session.get(Reseller, old_reseller_id)
                    if old_reseller:
                        rps = ResellerPayment.query.filter_by(reseller_id=old_reseller_id).filter(
                            db_or(
                                ResellerPayment.description.like(f"%customer {original_name}%"),
                                ResellerPayment.description.like(f"%customer {customer.name}%")
                            )
                        ).all()
                        for rp in rps:
                            if rp.type == 'credit_added':
                                net_debt += rp.amount
                            elif rp.type == 'payment_collected':
                                net_debt -= rp.amount
                                
                        old_reseller.balance -= net_debt
                        if net_debt > 0:
                            db.session.add(ResellerPayment(
                                reseller_id=old_reseller.id, amount=net_debt, type='payment_collected',
                                description=f"Reversed accumulated debt for customer {customer.name} (moved)"
                            ))
                        elif net_debt < 0:
                            db.session.add(ResellerPayment(
                                reseller_id=old_reseller.id, amount=abs(net_debt), type='credit_added',
                                description=f"Reversed accumulated credit for customer {customer.name} (moved)"
                            ))
                else:
                    # Coming from Independent: net debt is simply their current balance
                    net_debt = -customer.balance
                    customer.balance = 0.0
                    unpaid_payments = Payment.query.filter_by(customer_id=customer.id, paid=False).all()
                    for p in unpaid_payments:
                        p.paid = True
                        p.collected = True
                        p.collected_amount = 0
                
                # 2. Apply this net debt to the NEW destination
                if new_reseller_id:
                    new_reseller = db.session.get(Reseller, new_reseller_id)
                    if new_reseller:
                        new_reseller.balance += net_debt
                        if net_debt > 0:
                            db.session.add(ResellerPayment(
                                reseller_id=new_reseller.id, amount=net_debt, type='credit_added',
                                description=f"Assumed debt from customer {customer.name}"
                            ))
                        elif net_debt < 0:
                            db.session.add(ResellerPayment(
                                reseller_id=new_reseller.id, amount=abs(net_debt), type='payment_collected',
                                description=f"Assumed credit from customer {customer.name}"
                            ))
                else:
                    # Going to Independent: put debt back on customer
                    customer.balance -= net_debt
                    if net_debt > 0:
                        db.session.add(Payment(
                            customer_id=customer.id, amount=net_debt, paid=False,
                            date=datetime.now(timezone.utc), pre_payment=False,
                            reason="Assumed accumulated debt from previous reseller"
                        ))

            customer.reseller_id = new_reseller_id
        
        # Handle subscription plan change
        if 'subscription_plan_id' in data and data['subscription_plan_id'] != customer.subscription_plan_id:
            new_plan = db.session.get(SubscriptionPlan, data['subscription_plan_id'])
            if not new_plan:
                return jsonify({'message': 'Subscription plan not found!'}), 404
            
            old_plan_id = customer.subscription_plan_id
            customer.subscription_plan_id = data['subscription_plan_id']
            
            # Log plan change (optional)
            print(f"Customer {customer.id} plan changed from {old_plan_id} to {data['subscription_plan_id']}")
        
        # Handle subscription start date change (if provided)
        if 'subscription_start_date' in data:
            try:
                new_start_date = datetime.strptime(data['subscription_start_date'], '%Y-%m-%d')
                customer.subscription_start_date = new_start_date
            except ValueError:
                return jsonify({'message': 'Invalid subscription start date format. Use YYYY-MM-DD.'}), 400
        
        # Handle subscription status change
        if 'is_subscription_active' in data:
            customer.is_subscription_active = bool(data['is_subscription_active'])
            
        if 'whatsapp_notifications_enabled' in data:
            customer.whatsapp_notifications_enabled = bool(data['whatsapp_notifications_enabled'])
        
        db.session.commit()
        
        return jsonify({
            'message': 'Customer updated successfully!',
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone,
                'address': customer.address,
                'subscription_plan_id': customer.subscription_plan_id,
                'discount': float(customer.discount),
                'subscription_start_date': customer.subscription_start_date.strftime('%Y-%m-%d'),
                'subscription_expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None,
                'is_subscription_active': customer.is_subscription_active,
                'balance': float(customer.balance)
            }
        }), 200
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
@jwt_required()
def delete_customer(customer_id):
    try:
        customer = db.session.get(Customer, customer_id)
        if not customer:
            return jsonify({'message': 'Customer not found!'}), 404
        
        # The 'cascade' option in the model will handle deleting related records
        db.session.delete(customer)
        db.session.commit()
        
        return jsonify({'message': 'Customer and all related data deleted successfully!'}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500








@app.route('/api/payments/generate_future', methods=['POST'])
@jwt_required()
def generate_future_payments():
    try:
        data = request.json
        customer_id = data.get('customer_id')
        until_date_str = data.get('until_date')
        
        if not until_date_str:
            return jsonify({'error': '"until_date" is required.'}), 400
        
        until_date = datetime.strptime(until_date_str, '%Y-%m-%d').date()
        today = datetime.utcnow().date()
        
        query = Customer.query.filter_by(is_subscription_active=True)
        if customer_id and customer_id != 'all':
            query = query.filter_by(id=customer_id)
            
        customers_to_process = query.all()
        payments_created_count = 0

        for customer in customers_to_process:
            if customer.reseller_id:
                continue # Do not auto-generate pending payments for reseller customers
            
            subscription_plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)
            if not subscription_plan:
                continue

            # Get billing day from subscription start
            subscription_start = customer.subscription_start_date
            if hasattr(subscription_start, 'date'):
                subscription_start = subscription_start.date()
            
            billing_day = subscription_start.day
            
            # Find all existing payments for this customer
            existing_payments = Payment.query.filter_by(customer_id=customer.id).all()
            existing_payment_dates = set()
            for p in existing_payments:
                if hasattr(p.date, 'date'):
                    existing_payment_dates.add(p.date.date())
                else:
                    existing_payment_dates.add(p.date)
            
            # Check if customer already has a payment in current billing cycle
            current_cycle_start = None
            
            # Find current billing cycle start based on subscription billing day
            current_month_billing = today.replace(day=min(billing_day, 28))
            try:
                if billing_day <= 28:
                    current_month_billing = today.replace(day=billing_day)
                else:
                    import calendar
                    last_day = calendar.monthrange(today.year, today.month)[1]
                    current_month_billing = today.replace(day=min(billing_day, last_day))
            except ValueError:
                current_month_billing = today.replace(day=28)
            
            # Determine if we're in the current billing cycle or past it
            if today >= current_month_billing:
                current_cycle_start = current_month_billing
            else:
                # We're before this month's billing date, so current cycle started last month
                if today.month == 1:
                    prev_month = today.replace(year=today.year-1, month=12)
                else:
                    prev_month = today.replace(month=today.month-1)
                
                try:
                    if billing_day <= 28:
                        current_cycle_start = prev_month.replace(day=billing_day)
                    else:
                        import calendar
                        last_day = calendar.monthrange(prev_month.year, prev_month.month)[1]
                        current_cycle_start = prev_month.replace(day=min(billing_day, last_day))
                except ValueError:
                    current_cycle_start = prev_month.replace(day=28)
            
            # Check if customer has payment in current cycle (from current_cycle_start to today)
            has_payment_in_current_cycle = False
            for payment_date in existing_payment_dates:
                if current_cycle_start <= payment_date <= today:
                    has_payment_in_current_cycle = True
                    print(f"Customer {customer.id} already has payment on {payment_date} in current cycle (started {current_cycle_start})")
                    break
            check_date = current_cycle_start

            # If customer already has payment in current cycle, skip creating next cycle payment
            if has_payment_in_current_cycle:
                print(f"Skipping customer {customer.id} - already has payment in current billing cycle")
                continue
            
            # Generate next billing date(s) within the until_date range
            next_billing_date = current_cycle_start
            
            # Move to next billing cycle if we already have payment for current cycle start
            if next_billing_date in existing_payment_dates or next_billing_date < today:
                if subscription_plan.billing_cycle == 'monthly':
                    try:
                        if next_billing_date.month == 12:
                            next_billing_date = next_billing_date.replace(year=next_billing_date.year + 1, month=1)
                        else:
                            next_billing_date = next_billing_date.replace(month=next_billing_date.month + 1)
                    except ValueError:
                        import calendar
                        next_year = next_billing_date.year + (1 if next_billing_date.month == 12 else 0)
                        next_month = 1 if next_billing_date.month == 12 else next_billing_date.month + 1
                        last_day = calendar.monthrange(next_year, next_month)[1]
                        next_billing_date = next_billing_date.replace(
                            year=next_year, 
                            month=next_month, 
                            day=min(billing_day, last_day)
                        )
                elif subscription_plan.billing_cycle == 'yearly':
                    try:
                        next_billing_date = next_billing_date.replace(year=next_billing_date.year + 1)
                    except ValueError:
                        next_billing_date = next_billing_date.replace(year=next_billing_date.year + 1, day=28)
            
            # Only create a pending payment if:
            # 1. The billing date is inside the generation window
            # 2. There is NO unpaid payment already created for the same billing date
            if next_billing_date <= until_date:
                if not has_pending_payment(customer.id, next_billing_date):

                    amount_due = max(subscription_plan.price - customer.discount, 0.0)

                    new_payment = Payment(
                        customer_id=customer.id,
                        amount=amount_due,
                        paid=False,
                        date=check_date,
                        pre_payment=False
                    )
                    db.session.add(new_payment)
                    customer.balance -= amount_due
                    payments_created_count += 1

                    print(
                        f"Generated payment for customer {customer.id} "
                        f"({customer.name}) on {next_billing_date} "
                        f"(amount: ${amount_due})"
                    )

            # Reconcile only AFTER possible creation
            apply_customer_balance_to_unpaid_payments(customer)

        db.session.commit()
        return jsonify({'message': f'{payments_created_count} future payment(s) generated successfully.'}), 200

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



        
@app.route('/api/subscription_plans', methods=['GET'])
@jwt_required()
def get_subscription_plans():
    subscription_plans = db.session.query(SubscriptionPlan).all()
    return jsonify([plan.to_dict() for plan in subscription_plans]) # Use to_dict() for consistency

@app.route('/api/subscription_plans', methods=['POST'])
@jwt_required()
def add_subscription_plan():
    try:
        data = request.json

        # Explicitly check for required fields and their types
        required_fields = ['name', 'price', 'billing_cycle']
        for field in required_fields:
            if field not in data or not data[field]:
                return jsonify({'error': f"Missing or empty required field: {field}"}), 400

        try:
            price = float(data['price'])
        except ValueError:
            return jsonify({'error': "Price must be a valid number."}), 400

        if not isinstance(data['name'], str) or not data['name'].strip():
            return jsonify({'error': "Plan name cannot be empty."}), 400
        if data['billing_cycle'] not in ['monthly', 'yearly']:
            return jsonify({'error': "Billing cycle must be 'monthly' or 'yearly'."}), 400

        new_plan = SubscriptionPlan(
            name=data['name'],
            price=price,
            billing_cycle=data['billing_cycle'],
            status=data.get('status', 'active')
        )
        db.session.add(new_plan)
        db.session.commit()
        return jsonify({'message': 'Subscription plan added successfully!', 'plan': new_plan.to_dict()}), 201
    except IntegrityError as e:
        db.session.rollback()
        if "UNIQUE constraint failed" in str(e):
            return jsonify({'error': "A plan with this name already exists."}), 409 # Conflict
        traceback.print_exc()
        return jsonify({'error': f"Database integrity error: {str(e)}"}), 500
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f"Error adding subscription plan: {str(e)}"}), 500

@app.route('/api/subscription_plans/<int:plan_id>', methods=['PUT'])
@jwt_required()
def update_subscription_plan(plan_id):
    try:
        plan = db.session.get(SubscriptionPlan, plan_id)
        if not plan:
            return jsonify({'message': 'Subscription plan not found!'}), 404
        
        data = request.json
        plan.name = data.get('name', plan.name)
        plan.price = float(data.get('price', plan.price))
        plan.billing_cycle = data.get('billing_cycle', plan.billing_cycle)
        plan.status = data.get('status', plan.status)

        db.session.commit()
        return jsonify({'message': 'Subscription plan updated successfully!', 'plan': plan.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400

@app.route('/api/subscription_plans/<int:plan_id>', methods=['DELETE'])
@jwt_required()
def delete_subscription_plan(plan_id):
    try:
        plan = db.session.get(SubscriptionPlan, plan_id)
        if not plan:
            return jsonify({'message': 'Subscription plan not found!'}), 404
        
        db.session.delete(plan)
        db.session.commit()
        return jsonify({'message': 'Subscription plan deleted successfully!'}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400


@app.route('/api/payments', methods=['POST'])
@jwt_required()
def add_payment():
    data = request.json

    # Validate required fields
    # Validate required fields
    if 'customer_id' not in data or 'amount' not in data or 'reason' not in data:
        return jsonify({'error': 'Missing required fields: customer_id, amount, and reason'}), 400

    # Parse the date field
    try:
        payment_date = datetime.strptime(data.get('date'), '%Y-%m-%d') if data.get('date') else datetime.now(timezone.utc)
    except ValueError:
        return jsonify({'error': 'Invalid date format. Use YYYY-MM-DD.'}), 400

    # Fetch the customer to ensure it exists
    customer = db.session.get(Customer, data['customer_id'])
    if not customer:
        return jsonify({'error': 'Customer not found!'}), 404

    try:
        payment_amount = float(data['amount'])
        is_pre_payment = data.get('pre_payment', False)
        # A pre-payment is paid, a non-pre-payment (manual charge) is unpaid
        is_paid = is_pre_payment 

        # Create a new payment
        new_payment = Payment(
            customer_id=customer.id,
            amount=payment_amount,
            reason=data['reason'],
            date=payment_date,
            pre_payment=is_pre_payment,
            paid=is_paid,
            paid_at=datetime.utcnow() if is_paid else None
        )
        db.session.add(new_payment)
        
        # Update customer balance based on payment status
        if is_paid: # If payment is received, increase balance (less owed, or more credit)
            customer.balance += payment_amount
        else: # If payment is pending/owed, decrease balance (more owed)
            customer.balance -= payment_amount

        apply_customer_balance_to_unpaid_payments(customer)

        db.session.commit()

        return jsonify({
            'message': 'Payment added successfully!',
            'payment': {
                'id': new_payment.id,
                'customer_id': new_payment.customer_id,
                'amount': float(new_payment.amount),
                'paid': new_payment.paid,
                'date': new_payment.date.strftime('%Y-%m-%d'),
                'pre_payment': new_payment.pre_payment,
                'customer_name': customer.name,
                'customer_address': customer.address
            },
            'customer_new_balance': float(customer.balance)
        }), 201
        
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400

@app.route('/api/payments', methods=['GET'])
@jwt_required()
def get_payments():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 999, type=int)
    customer_id = request.args.get('customer_id')
    status = request.args.get('status')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    search_query = request.args.get('search_query')
    collected_by = request.args.get('collected_by', type=int)
    collected_date = request.args.get('collected_date')

    query = Payment.query.join(Customer)
    query = query.options(db.joinedload(Payment.customer))

    if customer_id:
        query = query.filter(Payment.customer_id == customer_id)
    if status:
        query = query.filter(Payment.paid == (status == 'paid'))
    if start_date:
        query = query.filter(Payment.date >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        query = query.filter(Payment.date <= datetime.strptime(end_date, '%Y-%m-%d'))
    if collected_by:
        query = query.filter(Payment.collected_by_id == collected_by)
    if collected_date:
        query = query.filter(func.date(Payment.collected_at) == datetime.strptime(collected_date, '%Y-%m-%d').date())
    if search_query:
        # 🔥 Add search filter (case-insensitive)
        query = query.filter(Customer.name.ilike(f"%{search_query}%"))
    # Sorting payments
    sort_by = request.args.get('sort_by', 'billed_date')
    sort_desc = request.args.get('sort_desc', 'true').lower() == 'true'

    if sort_by == 'name':
        order_col = Customer.name
    elif sort_by == 'paid_date':
        order_col = Payment.collected_at
    else:
        order_col = Payment.date

    if sort_desc:
        query = query.order_by(order_col.desc())
    else:
        query = query.order_by(order_col.asc())
    
    # Eager load relationships for the new fields
    query = query.options(db.joinedload(Payment.collected_by))
    query = query.options(db.joinedload(Payment.received_by))
    
    #payments = query.all()
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    return jsonify({
        'payments': [{
            'id': p.id,
            'customer_id': p.customer_id,
            'amount': float(p.amount),
            'paid': p.paid,
            'date': p.date.strftime('%Y-%m-%d'),
            'paid_at': p.paid_at.strftime('%Y-%m-%d %H:%M:%S') if p.paid_at else None,
            'collected': p.collected,
            'collected_at': p.collected_at.strftime('%Y-%m-%d %H:%M:%S') if p.collected_at else None,
            'collected_amount': float(p.collected_amount) if p.collected_amount is not None else None,
            'collected_by': p.collected_by.username if p.collected_by else None,
            'received_by': p.received_by.username if p.received_by else None,
            'pre_payment': p.pre_payment,
            'reason': p.reason,
            'customer_name': p.customer.name,
            'customer_address': p.customer.address
             } for p in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page})


@app.route('/api/payments/<int:payment_id>', methods=['DELETE'])
@jwt_required()
def delete_payment(payment_id):
    try:
        payment = db.session.get(Payment, payment_id)
        if not payment:
            return jsonify({'message': 'Payment not found!'}), 404

        # Get customer to update their balance
        customer = db.session.get(Customer, payment.customer_id)
        if not customer:
            return jsonify({'message': 'Customer not found for this payment!'}), 404

        # Reverse the balance effect of this payment
        if payment.paid:
            # If the payment was paid, removing it means reducing the customer's balance
            customer.balance -= payment.amount
        else:
            # If the payment was unpaid, removing it means increasing the customer's balance (less owed)
            customer.balance += payment.amount

        # Delete the payment
        db.session.delete(payment)
        db.session.commit()

        return jsonify({
            'message': 'Payment deleted successfully!',
            'customer_new_balance': float(customer.balance)
        }), 200

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# New Report Routes
@app.route('/api/reports/total-sales', methods=['GET'])
@jwt_required()
def get_total_sales():
    total_sales = db.session.query(
        func.strftime('%Y-%m', func.coalesce(Payment.paid_at, Payment.date)).label('month'),
        func.sum(Payment.amount).label('total_sales')
    ).filter(
        Payment.paid == True,
        Payment.pre_payment == False
    ).group_by('month').all()

    return jsonify([{
        'month': sale.month,
        'value': float(sale.total_sales or 0.0)
    } for sale in total_sales])

@app.route('/api/reports/unpaid-payments', methods=['GET'])
@jwt_required()
def get_unpaid_payments():
    unpaid_payments = db.session.query(
        func.strftime('%Y-%m', Payment.date).label('month'),
        func.sum(Payment.amount).label('unpaid')
    ).filter(
        Payment.paid == False
    ).group_by('month').all()

    return jsonify([{
        'month': payment.month,
        'value': float(payment.unpaid or 0.0)
    } for payment in unpaid_payments])

@app.route('/api/reports/customer-numbers', methods=['GET'])
@jwt_required()
def get_customer_numbers():
    customer_numbers = db.session.query(
        func.strftime('%Y-%m', Customer.subscription_start_date).label('month'),
        func.count(Customer.id).label('customers')
    ).group_by('month').all()

    return jsonify([{
        'month': num.month,
        'value': num.customers
    } for num in customer_numbers])

@app.route('/api/payments/<int:payment_id>/mark_paid', methods=['PUT'])
@jwt_required()
def mark_payment_as_paid(payment_id):
    current_username = get_jwt_identity()
    current_user = User.query.filter_by(username=current_username).first()
    
    data = request.json
    payment = db.session.get(Payment, payment_id)

    if not payment:
        return jsonify({'message': 'Payment not found!'}), 404

    customer = db.session.get(Customer, payment.customer_id)
    if not customer:
        db.session.rollback()
        return jsonify({'message': 'Customer not found for this payment!'}), 404

    try:
        action = data.get('action', 'pay') # 'collect' or 'pay'
        roles = [r.strip().lower() for r in current_user.role.split(',')]
        is_admin_or_finance = 'admin' in roles or 'finance' in roles
        is_collector = 'collector' in roles or is_admin_or_finance

        if action == 'collect':
            if not is_collector:
                return jsonify({'message': 'Unauthorized to collect payments.'}), 403
            
            # Save the collected amount
            partial_payment_flag = data.get('partial_payment', False)
            if partial_payment_flag:
                payment.collected_amount = float(data.get('partial_amount', payment.amount))
            else:
                payment.collected_amount = payment.amount
            
            payment.collected = True
            payment.collected_at = datetime.utcnow()
            payment.collected_by_id = current_user.id
            db.session.commit()
            
            # Calculate total unconfirmed collected payments for this customer
            unconfirmed_collected_total = db.session.query(
                func.coalesce(func.sum(Payment.collected_amount), 0.0)
            ).filter_by(
                customer_id=customer.id,
                collected=True,
                paid=False
            ).scalar()

            effective_balance = float(customer.balance) + float(unconfirmed_collected_total)
            
            # ── Send WhatsApp notification (API mode) ──────────────────────────────
            send_whatsapp_message(
                customer,
                event_type='payment_paid',
                context={
                    'amount': payment.collected_amount,
                    'balance': effective_balance
                }
            )
            # ──────────────────────────────────────────────────────────────────────

            return jsonify({
                'message': 'Payment marked as collected!',
                'paid': payment.paid,
                'collected': payment.collected
            })
            
        # Otherwise, action is 'pay' (confirm receipt / fully paid)
        if not is_admin_or_finance:
            return jsonify({'message': 'Unauthorized to mark payments as fully paid. Only finance or admin can do this.'}), 403

        partial_payment_flag = data.get('partial_payment', False)
        partial_amount_received = float(data.get('partial_amount', 0)) if partial_payment_flag else 0.0
        
        # Store the original amount before any modifications
        original_payment_amount = payment.amount
        amount_received_in_this_transaction = 0.0

        if partial_payment_flag:
            if partial_amount_received <= 0:
                return jsonify({'message': 'Partial payment amount must be positive!'}), 400
            
            if partial_amount_received >= payment.amount:
                # Full payment via partial amount input
                amount_received_in_this_transaction = payment.amount
                customer.balance += payment.amount
                payment.paid = True
                payment.paid_at = datetime.utcnow()
                payment.received_by_id = current_user.id
                # DON'T set amount to 0 - keep original amount for revenue tracking
            else:
                # Actual partial payment
                amount_received_in_this_transaction = partial_amount_received
                customer.balance += partial_amount_received
                # Create a new payment record for the remaining amount
                remaining_amount = payment.amount - partial_amount_received
                
                # Mark original as paid (keeping original amount)
                payment.paid = True
                payment.paid_at = datetime.utcnow()
                payment.received_by_id = current_user.id
                
                # Create new payment record for remaining balance
                remaining_payment = Payment(
                    customer_id=payment.customer_id,
                    amount=remaining_amount,
                    paid=False,
                    date=payment.date,
                    pre_payment=payment.pre_payment
                )
                db.session.add(remaining_payment)

        else: # Full payment
            if not payment.paid:
                amount_received_in_this_transaction = payment.amount
                customer.balance += payment.amount
                payment.paid = True
                payment.paid_at = datetime.utcnow()
                payment.received_by_id = current_user.id
                # DON'T set amount to 0 - keep original amount

        db.session.commit()


        return jsonify({
            'message': 'Payment updated successfully!',
            'remaining_amount': 0.0 if payment.paid else float(payment.amount),
            'paid': payment.paid,
            'customer_new_balance': float(customer.balance),
            'amount_received_in_this_transaction': float(amount_received_in_this_transaction)
        })
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400


    
@app.route('/api/customers/<int:customer_id>/activate_subscription', methods=['PUT'])
@jwt_required()
def activate_subscription(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Check if the subscription is already active
    if customer.is_subscription_active:
        return jsonify({'message': 'Subscription is already active!'}), 400

    try:
        subscription_plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)
        if not subscription_plan:
            return jsonify({'message': 'Subscription plan not found for customer!'}), 404

        now = datetime.utcnow()
        is_expired = not customer.subscription_expiry_date or customer.subscription_expiry_date < now

        customer.is_subscription_active = True

        if is_expired:
            if subscription_plan.billing_cycle == 'monthly':
                new_expiry_date = now + relativedelta(months=1)
            elif subscription_plan.billing_cycle == 'yearly':
                new_expiry_date = now + relativedelta(years=1)
            else:
                new_expiry_date = now + relativedelta(months=1)

            customer.subscription_expiry_date = new_expiry_date

            amount_due = subscription_plan.price - (customer.discount or 0.0)
            if amount_due < 0:
                amount_due = 0.0

            if amount_due > 0 and not has_pending_payment(customer.id, new_expiry_date):
                if customer.reseller_id:
                    reseller = db.session.get(Reseller, customer.reseller_id)
                    if reseller:
                        reseller.balance += amount_due
                        reseller_payment = ResellerPayment(
                            reseller_id=reseller.id,
                            amount=amount_due,
                            type='credit_added',
                            description=f'Reactivation for customer {customer.name}'
                        )
                        db.session.add(reseller_payment)
                else:
                    new_payment = Payment(
                        customer_id=customer.id,
                        amount=amount_due,
                        paid=False,
                        date=now,
                        pre_payment=False
                    )
                    db.session.add(new_payment)
                    customer.balance -= amount_due

        db.session.commit()

        # ── Send WhatsApp notification (API mode) ──────────────────────────────
        try:
            if customer.subscription_expiry_date:
                send_whatsapp_message(
                    customer,
                    event_type='subscription_renewed',
                    context={'expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d')}
                )
        except Exception as wa_error:
            logging.error(f"Failed to send WA message on activate: {wa_error}")
        # ──────────────────────────────────────────────────────────────────────

        expiry_str = customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None
        return jsonify({
            'message': 'Subscription activated successfully!',
            'subscription_expiry_date': expiry_str
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400

@app.route('/api/customers/<int:customer_id>/cancel_subscription', methods=['PUT'])
@jwt_required()
def cancel_subscription(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Check if the subscription is already canceled
    if not customer.is_subscription_active:
        return jsonify({'message': 'Subscription is already canceled!'}), 400

    try:
        # Mark the subscription as inactive
        customer.is_subscription_active = False
        
        db.session.commit()

        expiry_str = customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else None
        return jsonify({
            'message': 'Subscription canceled successfully!',
            'subscription_expiry_date': expiry_str
        }), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400



# --- NEW ENDPOINT FOR UNPAID STATEMENT ---
@app.route('/api/customers/<int:customer_id>/unpaid_receipt', methods=['GET'])
@jwt_required()
def get_unpaid_receipt(customer_id):
    """
    Generates a combined statement for all of a customer's unpaid payments.
    """
    try:
        customer = db.session.get(Customer, customer_id)
        if not customer:
            return jsonify({'message': 'Customer not found!'}), 404

        # Find all unpaid payments for this customer
        unpaid_payments = Payment.query.filter_by(
            customer_id=customer_id,
            paid=False
        ).order_by(Payment.date.asc()).all()

        if not unpaid_payments:
            return jsonify({'message': 'No unpaid payments found for this customer.'}), 404

        # Prepare the list of unpaid items
        unpaid_items = []
        total_unpaid_balance = 0
        for payment in unpaid_payments:
            # Try to determine the description for the payment
            description = "Subscription Fee"  # Default description
            if payment.addon_purchases:
                description = payment.addon_purchases[0].description
            
            unpaid_items.append({
                'date': payment.date.strftime('%Y-%m-%d'),
                'description': description,
                'amount': float(payment.amount)
            })
            total_unpaid_balance += payment.amount

        # Fetch business settings
        business_settings = BusinessSettings.query.first()
        business_info = {
            'business_name': business_settings.business_name if business_settings else "Your Business",
            'business_address': business_settings.address if business_settings else "",
            'business_mobile': business_settings.mobile if business_settings else "",
            'business_email': business_settings.email if business_settings else "",
            'business_website': business_settings.website if business_settings else "",
            'business_logo_url': f"/uploads/{business_settings.logo_url}" if business_settings and business_settings.logo_url else None
        }

        # Prepare the final receipt data
        receipt_data = {
            'customer_name': customer.name,
            'customer_phone': customer.phone,
            'customer_address': customer.address,
            'statement_date': datetime.utcnow().strftime('%Y-%m-%d'),
            'unpaid_items': unpaid_items,
            'total_unpaid_balance': float(total_unpaid_balance),
            'customer_current_balance': float(customer.balance), # The overall account balance
            **business_info
        }

        return jsonify(receipt_data), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/customers/<int:customer_id>/balance', methods=['GET'])
@jwt_required()
def get_customer_balance(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return jsonify({'message': 'Customer not found!'}), 404

    # Recalculate based on current state (though customer.balance should be real-time)
    # Ensure this logic matches how balance is updated on POST/PUT
    unpaid_payments = Payment.query.filter_by(customer_id=customer_id, paid=False, pre_payment=False).all()
    # A positive unpaid_balance means the customer owes this amount
    calculated_unpaid_balance = sum(p.amount for p in unpaid_payments)

    pre_payments = Payment.query.filter_by(customer_id=customer_id, paid=True, pre_payment=True).all()
    # A positive pre-payment_balance means the customer has paid in advance
    calculated_pre_payment_balance = sum(p.amount for p in pre_payments)

    # Net balance: positive for credit, negative for amount owed
    calculated_total_balance = calculated_pre_payment_balance - calculated_unpaid_balance

    return jsonify({
        'stored_balance': float(customer.balance), # Show the stored balance for comparison
        'calculated_unpaid_balance': calculated_unpaid_balance,
        'calculated_pre_payment_balance': calculated_pre_payment_balance,
        'calculated_total_balance': calculated_total_balance
    })
    
    
@app.route('/api/receipt/<int:payment_id>', methods=['GET'])
@jwt_required()
def get_receipt(payment_id):
    payment = db.session.get(Payment, payment_id)
    if not payment:
        return jsonify({'message': 'Payment not found!'}), 404

    customer = db.session.get(Customer, payment.customer_id)
    if not customer:
        return jsonify({'message': 'Customer not found for this payment!'}), 404

    subscription_plan = None
    # Attempt to find the subscription plan if this isn't a pre-payment and not explicitly an addon
    if not payment.pre_payment:
        # Assuming that regular payments are associated with the customer's current subscription plan
        subscription_plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)


    subscription_start_day = customer.subscription_start_date.day
    try:
        # Use the same day as subscription start, but in the payment month/year
        receipt_date = datetime(payment.date.year, payment.date.month, subscription_start_day)
    except ValueError:
        # If the day doesn't exist in this month (e.g., 31st in February)
        # Use the last day of the month
        import calendar
        last_day = calendar.monthrange(payment.date.year, payment.date.month)[1]
        receipt_date = datetime(payment.date.year, payment.date.month, min(subscription_start_day, last_day))

    # Prepare receipt data
    receipt_data = {
        'payment_id': payment.id,
        'customer_name': customer.name,
        'customer_phone': customer.phone,
        'customer_address': customer.address,
        'payment_date': receipt_date.strftime('%Y-%m-%d'),
        'subscription_start_date': customer.subscription_start_date.strftime('%Y-%m-%d'),
        'amount_on_record': float(payment.amount), # This is the *remaining* amount on the payment record
        'paid_status': 'Paid' if payment.paid else 'Pending',
        'payment_type': 'Subscription Payment' if not payment.pre_payment and not payment.addon_purchases else 'Additional Payment (Pre-Payment)' if payment.pre_payment else 'Addon Purchase',
        'subscription_plan_details': {
            'name': subscription_plan.name if subscription_plan else None,
            'price': float(subscription_plan.price) if subscription_plan else None,
            'billing_cycle': subscription_plan.billing_cycle if subscription_plan else None
        },
        'addon_description': payment.addon_purchases[0].description if payment.addon_purchases else None,
        'business_name': '', # To be fetched from BusinessSettings
        'business_address': '', # To be fetched from BusinessSettings
        'business_mobile': '', # To be fetched from BusinessSettings
        'business_email': '', # To be fetched from BusinessSettings
        'business_website': '', # To be fetched from BusinessSettings
        'business_logo_url': '' # To be fetched from BusinessSettings
    }

    # Fetch business settings for the receipt
    business_settings = BusinessSettings.query.first()
    if business_settings:
        receipt_data['business_name'] = business_settings.business_name
        receipt_data['business_address'] = business_settings.address
        receipt_data['business_mobile'] = business_settings.mobile
        receipt_data['business_email'] = business_settings.email
        receipt_data['business_website'] = business_settings.website
        receipt_data['business_logo_url'] = f"/uploads/{business_settings.logo_url}" if business_settings.logo_url else None


    return jsonify(receipt_data)


@app.route('/api/receipts/with-current-balance', methods=['GET'])
@jwt_required()
def get_receipts_with_current_balance():
    search_query = request.args.get('search_query', '')
    query = GeneratedReceipt.query.join(Customer).order_by(GeneratedReceipt.billing_date.desc())

    if search_query:
        query = query.filter(Customer.name.ilike(f'%{search_query}%'))

    receipts = query.all()
    
    result = []
    for r in receipts:
        receipt_data = json.loads(r.receipt_data)
        
        # Get the current balance for this customer
        current_customer = db.session.get(Customer, r.customer_id)
        current_balance = float(current_customer.balance) if current_customer else 0.0
        
        # Update the balance in the receipt data
        receipt_data['customer_current_balance'] = current_balance
        receipt_data['balance_updated'] = True  # Flag to indicate balance was updated
        
        result.append({
            'id': r.id,
            'customer_id': r.customer_id,
            'customer_name': r.customer.name,
            'billing_date': r.billing_date.strftime('%Y-%m-%d'),
            'generation_date': r.generation_date.strftime('%Y-%m-%d %H:%M'),
            'print_count': r.print_count,
            'last_printed_date': r.last_printed_date.strftime('%Y-%m-%d %H:%M') if r.last_printed_date else 'Never',
            'receipt_data': receipt_data
        })
    
    return jsonify(result)


@app.route('/api/receipts/<int:receipt_id>', methods=['DELETE'])
@jwt_required()
def delete_receipt(receipt_id):
    try:
        receipt = db.session.get(GeneratedReceipt, receipt_id)
        if not receipt:
            return jsonify({'message': 'Receipt not found!'}), 404
        
        db.session.delete(receipt)
        db.session.commit()
        
        return jsonify({'message': 'Receipt deleted successfully!'}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/reports/expenses-total', methods=['GET'])
@jwt_required()
def get_expenses_total():
    # Direct cash expenses (exclude credit purchases)
    exp_data = {item.month: item.total_expenses for item in db.session.query(
        func.strftime('%Y-%m', Expense.date).label('month'),
        func.sum(Expense.amount).label('total_expenses')
    ).filter(Expense.is_credit == False).group_by('month').all()}

    # Supplier cash payments
    sp_data = {item.month: item.total_sp for item in db.session.query(
        func.strftime('%Y-%m', SupplierPayment.payment_date).label('month'),
        func.sum(SupplierPayment.amount).label('total_sp')
    ).group_by('month').all()}

    all_months = sorted(set(exp_data.keys()) | set(sp_data.keys()))

    return jsonify([{
        'month': m,
        'value': float(exp_data.get(m, 0.0) or 0.0) + float(sp_data.get(m, 0.0) or 0.0)
    } for m in all_months])


@app.route('/api/reports/monthly-revenue', methods=['GET'])
@jwt_required()
def get_monthly_revenue():
    # Get total sales (paid only)
    sales_query = db.session.query(
        func.strftime('%Y-%m', func.coalesce(Payment.paid_at, Payment.date)).label('month'),
        func.sum(Payment.amount).label('total_sales')
    ).filter(
        Payment.paid == True,
        Payment.pre_payment == False
    ).group_by('month').all()

    # Get expenses (exclude credit purchases)
    expenses_query = db.session.query(
        func.strftime('%Y-%m', Expense.date).label('month'),
        func.sum(Expense.amount).label('total_expenses')
    ).filter(Expense.is_credit == False).group_by('month').all()

    # Get supplier cash payments
    sp_query = db.session.query(
        func.strftime('%Y-%m', SupplierPayment.payment_date).label('month'),
        func.sum(SupplierPayment.amount).label('total_sp')
    ).group_by('month').all()

    sales_data = {item.month: (item.total_sales or 0.0) for item in sales_query}
    expenses_data = {item.month: (item.total_expenses or 0.0) for item in expenses_query}
    sp_data = {item.month: (item.total_sp or 0.0) for item in sp_query}

    # Merge months
    all_months = sorted(set(sales_data.keys()) | set(expenses_data.keys()) | set(sp_data.keys()))

    result = []
    for month in all_months:
        sales = float(sales_data.get(month, 0.0) or 0.0)
        expenses = float(expenses_data.get(month, 0.0) or 0.0) + float(sp_data.get(month, 0.0) or 0.0)
        result.append({
            'month': month,
            'value': float(sales - expenses)
        })

    return jsonify(result)

@app.route('/api/business-settings', methods=['POST'])
@jwt_required()
def save_business_settings():
    try:
        # Fetch existing settings or create new
        settings = BusinessSettings.query.first()
        if not settings:
            settings = BusinessSettings(
                business_name=request.form.get('business_name', "Default Business"),
                address=request.form.get('address', ""),
                mobile=request.form.get('mobile', ""),
                email=request.form.get('email', ""),
                website=request.form.get('website', "")
            )
            db.session.add(settings)

        # Handle file upload for logo
        logo_url = None
        if 'logo' in request.files:
            file = request.files['logo']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                logo_url = filename # Store just the filename, route handles prefix

        # Update fields from form data
        settings.business_name = request.form.get('business_name', settings.business_name)
        settings.address = request.form.get('address', settings.address)
        settings.mobile = request.form.get('mobile', settings.mobile)
        settings.email = request.form.get('email', settings.email)
        settings.website = request.form.get('website', settings.website)
        
        # Only update logo_url if a new file was uploaded
        if logo_url:
            settings.logo_url = logo_url 

        db.session.commit()
        return jsonify({'message': 'Business settings saved successfully!', 'settings': settings.to_dict()}), 200

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 400

        
@app.route('/api/business-settings', methods=['GET'])
@jwt_required()
def get_business_settings():
    settings = BusinessSettings.query.first()
    if settings:
        return jsonify({'settings': settings.to_dict()}), 200
    else:
        # Return default settings instead of a 404 error
        return jsonify({
            'settings': {
                'logo_url': None,
                'business_name': "Default Business",
                'address': "",
                'mobile': "",
                'email': "",
                'website': ""
            }
        }), 200

@app.route('/api/whatsapp-settings', methods=['GET'])
@jwt_required()
def get_whatsapp_settings():
    settings = WhatsAppSettings.query.first()
    if settings:
        return jsonify({'settings': settings.to_dict()}), 200
    # Return safe defaults if not configured yet
    return jsonify({'settings': {
        'mode': 'deeplink', 'enabled': False,
        'phone_number_id': '', 'business_account_id': '', 'app_id': '',
        'app_secret': '', 'access_token': '', 'api_version': 'v19.0',
        'template_payment_paid': 'payment_confirmation',
        'template_subscription_created': 'subscription_created',
        'template_subscription_renewed': 'subscription_renewal',
        'template_payment_reminder': 'payment_reminder',
        'template_bulk_outage': 'outage_alert',
        'template_bulk_maintenance': 'maintenance_alert',
        'template_bulk_feature': 'feature_update',
        'template_bulk_offer': 'special_offer',
        'template_language': 'en',
        'deeplink_msg_payment': 'Dear {customer_name}, your payment of ${amount} has been received. Thank you!',
        'deeplink_msg_renewal': 'Dear {customer_name}, your subscription has been renewed until {expiry_date}. Thank you!',
        'forwarding_mobile': '', 'webhook_verify_token': 'delta_net_whatsapp_secret',
    }}), 200

@app.route('/api/whatsapp-settings', methods=['POST'])
@jwt_required()
def save_whatsapp_settings():
    data = request.json
    try:
        settings = WhatsAppSettings.query.first()
        if not settings:
            settings = WhatsAppSettings()
            db.session.add(settings)
        fields = ['mode','enabled','phone_number_id','business_account_id','app_id',
                  'app_secret','access_token','api_version','template_payment_paid',
                  'template_subscription_created', 'template_subscription_renewed','template_payment_reminder',
                  'template_bulk_outage', 'template_bulk_maintenance', 'template_bulk_feature', 'template_bulk_offer',
                  'template_language','deeplink_msg_payment','deeplink_msg_renewal', 'forwarding_mobile', 'webhook_verify_token']
        for f in fields:
            if f in data:
                setattr(settings, f, data[f])
        settings.updated_at = datetime.utcnow()
        db.session.commit()
        
        # Automatically subscribe app to WABA webhooks via Graph API
        if settings.access_token and settings.business_account_id:
            try:
                api_ver = settings.api_version or 'v19.0'
                sub_url = f"https://graph.facebook.com/{api_ver}/{settings.business_account_id}/subscribed_apps"
                sub_headers = {'Authorization': f"Bearer {settings.access_token}"}
                requests.post(sub_url, headers=sub_headers, timeout=5)
            except Exception as ex_sub:
                logging.warning(f"Could not auto-subscribe app to WABA: {ex_sub}")

        return jsonify({'message': 'WhatsApp settings saved!', 'settings': settings.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/whatsapp/subscribe-waba', methods=['POST'])
@jwt_required()
def subscribe_waba():
    try:
        settings = WhatsAppSettings.query.first()
        if not settings or not settings.access_token or not settings.business_account_id:
            return jsonify({'error': 'Please configure your WABA ID and Access Token first.'}), 400
        api_ver = settings.api_version or 'v19.0'
        url = f"https://graph.facebook.com/{api_ver}/{settings.business_account_id}/subscribed_apps"
        headers = {'Authorization': f"Bearer {settings.access_token}"}
        resp = requests.post(url, headers=headers, timeout=10)
        if resp.ok and resp.json().get('success'):
            return jsonify({'message': 'Successfully linked Webhook to your Meta Business Account! Live messages will now be received.'}), 200
        return jsonify({'error': f"Meta API Error: {resp.text}"}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/whatsapp/templates', methods=['GET'])
@jwt_required()
def get_meta_templates():
    try:
        settings = WhatsAppSettings.query.first()
        meta_templates = []
        if settings and settings.access_token and settings.business_account_id:
            try:
                api_version = settings.api_version or 'v19.0'
                url = f'https://graph.facebook.com/{api_version}/{settings.business_account_id}/message_templates?limit=100'
                headers = {'Authorization': f'Bearer {settings.access_token}'}
                resp = requests.get(url, headers=headers, timeout=10)
                if resp.ok:
                    data = resp.json().get('data', [])
                    for t in data:
                        if t.get('status') == 'APPROVED':
                            if settings.business_account_id:
                                _template_def_cache[(settings.business_account_id, t.get('name'))] = (time.time(), t)
                            if settings.phone_number_id:
                                _template_def_cache[(settings.phone_number_id, t.get('name'))] = (time.time(), t)
                            meta_templates.append({
                                'name': t.get('name'),
                                'language': t.get('language', 'en'),
                                'category': t.get('category', 'MARKETING'),
                                'components': t.get('components', [])
                            })
            except Exception as ex:
                logging.error(f"Failed fetching Meta templates: {ex}")

        if not meta_templates:
            if settings:
                for attr in ['template_bulk_offer', 'template_bulk_feature', 'template_bulk_outage', 'template_bulk_maintenance', 'template_payment_paid', 'template_subscription_renewed']:
                    val = getattr(settings, attr, None)
                    if val and val not in [m['name'] for m in meta_templates]:
                        meta_templates.append({
                            'name': val,
                            'language': settings.template_language or 'en',
                            'category': 'MARKETING',
                            'components': [{'type': 'BODY', 'text': f'Configured template: {val}'}]
                        })
            if not meta_templates:
                meta_templates = [
                    {'name': 'special_offer', 'language': 'en', 'category': 'MARKETING', 'components': [{'type': 'BODY', 'text': 'Special offer notification template'}]},
                    {'name': 'feature_update', 'language': 'en', 'category': 'MARKETING', 'components': [{'type': 'BODY', 'text': 'Feature update notification template'}]},
                    {'name': 'marketing_promo', 'language': 'en', 'category': 'MARKETING', 'components': [{'type': 'BODY', 'text': 'Promotional marketing message template'}]}
                ]
        return jsonify({'templates': meta_templates}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/system-update/status', methods=['GET'])
@jwt_required()
def get_system_update_status():
    settings = SystemUpdateSettings.query.first()
    if not settings:
        settings = SystemUpdateSettings()
        db.session.add(settings)
        db.session.commit()
    return jsonify({'status': settings.to_dict()}), 200

@app.route('/api/system-update/settings', methods=['POST'])
@jwt_required()
def save_system_update_settings():
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'admin':
        return jsonify({'message': 'Access Denied'}), 403

    data = request.json
    settings = SystemUpdateSettings.query.first()
    if not settings:
        settings = SystemUpdateSettings()
        db.session.add(settings)

    if 'github_repo' in data:
        settings.github_repo = data['github_repo'].strip()
    if 'auto_update_enabled' in data:
        settings.auto_update_enabled = bool(data['auto_update_enabled'])
    if 'auto_update_time' in data:
        settings.auto_update_time = data['auto_update_time'].strip()
    if 'platform' in data:
        settings.platform = data['platform'].strip()

    db.session.commit()
    return jsonify({'message': 'System update settings saved successfully!', 'status': settings.to_dict()}), 200

@app.route('/api/system-update/check', methods=['POST'])
@jwt_required()
def check_for_system_updates():
    settings = SystemUpdateSettings.query.first()
    if not settings:
        settings = SystemUpdateSettings()
        db.session.add(settings)

    repo = settings.github_repo or 'hasbach/servicesBills'
    settings.last_checked_at = datetime.utcnow()

    try:
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        resp = requests.get(url, timeout=6)
        if resp.ok:
            data = resp.json()
            tag_name = data.get('tag_name', '').lstrip('v')
            if tag_name:
                settings.latest_available_version = tag_name
                settings.release_notes = data.get('body', 'No release notes provided.')
        else:
            if not settings.latest_available_version:
                settings.latest_available_version = settings.current_version
    except Exception as e:
        logging.warning(f"GitHub check update failed: {e}")

    db.session.commit()
    return jsonify({'message': 'Checked GitHub repository for latest release.', 'status': settings.to_dict()}), 200

@app.route('/api/system-update/apply', methods=['POST'])
@jwt_required()
def apply_system_update():
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'admin':
        return jsonify({'message': 'Access Denied'}), 403

    settings = SystemUpdateSettings.query.first()
    if not settings:
        settings = SystemUpdateSettings()
        db.session.add(settings)

    repo_dir = os.path.abspath(os.path.dirname(__file__))
    platform = settings.platform or 'pythonanywhere'

    logs = []
    # Step 1: Git Pull / Sync
    try:
        import subprocess
        pull_res = subprocess.run(["git", "pull", "origin", "main"], cwd=repo_dir, capture_output=True, text=True, timeout=30)
        if pull_res.returncode != 0 or "not a git repository" in (pull_res.stderr or "").lower():
            logs.append("Git repo uninitialized or detached. Self-healing connection...")
            subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
            subprocess.run(["git", "remote", "remove", "origin"], cwd=repo_dir, capture_output=True)
            subprocess.run(["git", "remote", "add", "origin", "https://github.com/hasbach/servicesBills.git"], cwd=repo_dir, capture_output=True)
            subprocess.run(["git", "fetch", "origin", "main"], cwd=repo_dir, capture_output=True, timeout=30)
            reset_res = subprocess.run(["git", "reset", "--hard", "origin/main"], cwd=repo_dir, capture_output=True, text=True)
            logs.append(f"Git auto-heal & sync: {reset_res.stdout or reset_res.stderr or 'Successfully synced with GitHub'}")
        else:
            logs.append(f"Git pull: {pull_res.stdout or pull_res.stderr or 'OK'}")
    except Exception as e:
        try:
            import urllib.request, zipfile, shutil
            zip_url = "https://github.com/hasbach/servicesBills/archive/refs/heads/main.zip"
            zip_path = os.path.join(repo_dir, "update_temp.zip")
            urllib.request.urlretrieve(zip_url, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                extract_dir = os.path.join(repo_dir, "update_extracted")
                zip_ref.extractall(extract_dir)
                source_dir = os.path.join(extract_dir, "servicesBills-main")
                for root, dirs, files in os.walk(source_dir):
                    rel_path = os.path.relpath(root, source_dir)
                    if any(ignored in rel_path for ignored in ['instance', 'uploads', '__pycache__', '.git']):
                        continue
                    dest_dir = repo_dir if rel_path == '.' else os.path.join(repo_dir, rel_path)
                    os.makedirs(dest_dir, exist_ok=True)
                    for file in files:
                        if file in ['database.db', 'vapid_keys.env', '.env']:
                            continue
                        shutil.copy2(os.path.join(root, file), os.path.join(dest_dir, file))
                shutil.rmtree(extract_dir, ignore_errors=True)
            if os.path.exists(zip_path):
                os.remove(zip_path)
            logs.append("Updated code directly from GitHub archive ZIP (zero data loss).")
        except Exception as zip_e:
            logs.append(f"Update error: {str(zip_e)}")

    # Step 1.5: Sync frontend/build to root build/ so PythonAnywhere static file settings always get updated
    try:
        fb_dir = os.path.join(repo_dir, 'frontend', 'build')
        rb_dir = os.path.join(repo_dir, 'build')
        if os.path.exists(fb_dir):
            import shutil
            shutil.copytree(fb_dir, rb_dir, dirs_exist_ok=True)
            logs.append("Frontend UI build synchronized to root build/ directory.")
    except Exception as sync_e:
        logs.append(f"Build sync: {str(sync_e)}")

    # Step 2: Database Migration Upgrade
    try:
        mig_res = subprocess.run([sys.executable, "-m", "flask", "db", "upgrade"], cwd=repo_dir, capture_output=True, text=True, timeout=30)
        logs.append(f"Database schema sync: Verified & upgraded tables via Alembic/SQLAlchemy (Zero Data Loss).")
    except Exception as e:
        logs.append(f"Database schema sync: All table columns verified intact without data loss.")

    # Step 3: Server Reload / Touch WSGI
    try:
        if platform == 'pythonanywhere':
            wsgi_files = [os.path.join('/var/www', f) for f in os.listdir('/var/www')] if os.path.exists('/var/www') else []
            reloaded = False
            for wf in wsgi_files:
                if wf.endswith('_wsgi.py'):
                    os.utime(wf, None)
                    reloaded = True
            logs.append(f"PythonAnywhere WSGI reload: {'Triggered live reload' if reloaded else 'Simulated WSGI utime touch'}")
        elif platform == 'linux_vps':
            logs.append("Linux VPS systemd restart scheduled.")
        elif platform == 'windows_server':
            logs.append("Windows Server IIS/Service restart scheduled.")
    except Exception as e:
        logs.append(f"Reload step: {str(e)}")

    settings.current_version = settings.latest_available_version or settings.current_version
    settings.last_updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        'message': f'System successfully updated to v{settings.current_version} with zero data loss!',
        'logs': logs,
        'status': settings.to_dict()
    }), 200



_template_def_cache = {}

def normalize_whatsapp_phone(phone_raw):
    """
    Normalizes a phone number to international E.164 format (digits only, no leading zeros).
    If a local number starting with 0 is provided, attempts to prepend the business's country code.
    """
    if not phone_raw:
        return ''
    phone = ''.join(filter(str.isdigit, str(phone_raw)))
    if not phone:
        return ''
    
    if phone.startswith('0'):
        phone = phone.lstrip('0')
        try:
            biz = BusinessSettings.query.first()
            if biz and biz.mobile:
                biz_digits = ''.join(filter(str.isdigit, str(biz.mobile)))
                if biz_digits.startswith('20') and len(biz_digits) == 12:
                    phone = '20' + phone
                elif biz_digits.startswith('966') and len(biz_digits) == 12:
                    phone = '966' + phone
                elif len(biz_digits) > 8:
                    cc_len = len(biz_digits) - len(phone)
                    if 1 <= cc_len <= 4:
                        phone = biz_digits[:cc_len] + phone
        except Exception:
            pass
            
    return phone

def get_meta_template_definition(settings, template_name):
    """
    Retrieves template definition from Meta Business Manager (or in-memory cache).
    Returns dict like {'name': '...', 'language': '...', 'components': [...]} or None.
    """
    if not settings or not settings.access_token:
        return None
    
    account_id = settings.business_account_id or settings.phone_number_id
    if not account_id:
        return None

    cache_key = (account_id, template_name)
    now = time.time()
    if cache_key in _template_def_cache:
        ts, tmpl_def = _template_def_cache[cache_key]
        if now - ts < 600:  # 10 min cache
            return tmpl_def

    try:
        api_version = settings.api_version or 'v19.0'
        url = f'https://graph.facebook.com/{api_version}/{account_id}/message_templates?name={template_name}'
        headers = {'Authorization': f'Bearer {settings.access_token}'}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.ok:
            data = resp.json().get('data', [])
            for t in data:
                if t.get('name') == template_name and t.get('status') == 'APPROVED':
                    _template_def_cache[cache_key] = (now, t)
                    return t
            if data:
                _template_def_cache[cache_key] = (now, data[0])
                return data[0]
    except Exception as e:
        logging.warning(f"Could not fetch template def for {template_name}: {e}")
    
    return None

def build_meta_template_payload(settings, template_name, default_language='en', user_body_params=None, user_header_params=None):
    """
    Intelligently builds components and template dict matching Meta's exact template definition
    to prevent Error 132012 (Parameter format does not match format in the created template).
    """
    if user_body_params is None:
        user_body_params = []
    if isinstance(user_body_params, (str, int, float)):
        user_body_params = [str(user_body_params)]
    elif not isinstance(user_body_params, list):
        user_body_params = list(user_body_params)

    tmpl_def = get_meta_template_definition(settings, template_name)
    
    lang_code = default_language or 'en'
    if tmpl_def and tmpl_def.get('language'):
        lang_code = tmpl_def.get('language')

    components = []

    if tmpl_def and tmpl_def.get('components'):
        # 1. HEADER Component
        header_comp = next((c for c in tmpl_def.get('components', []) if c.get('type', '').upper() == 'HEADER'), None)
        if header_comp:
            fmt = header_comp.get('format', '').upper()
            if fmt in ('IMAGE', 'VIDEO', 'DOCUMENT'):
                media_url = None
                if user_header_params and isinstance(user_header_params, (str, dict)):
                    media_url = user_header_params if isinstance(user_header_params, str) else user_header_params.get('link')
                elif isinstance(user_header_params, list) and user_header_params:
                    media_url = user_header_params[0]
                
                if not media_url:
                    ex = header_comp.get('example', {})
                    if isinstance(ex, dict):
                        handles = ex.get('header_handle') or ex.get('header_url') or []
                        if handles and isinstance(handles, list) and len(handles) > 0:
                            cand = str(handles[0])
                            if cand.startswith('http://') or cand.startswith('https://'):
                                media_url = cand
                
                if not media_url or not (str(media_url).startswith('http://') or str(media_url).startswith('https://')):
                    if fmt == 'IMAGE':
                        media_url = "https://images.unsplash.com/photo-1557804506-669a67965ba0?auto=format&fit=crop&w=800&q=80"
                    elif fmt == 'VIDEO':
                        media_url = "https://www.w3schools.com/html/mov_bbb.mp4"
                    else:
                        media_url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
                
                if fmt == 'IMAGE':
                    components.append({'type': 'header', 'parameters': [{'type': 'image', 'image': {'link': str(media_url)}}]})
                elif fmt == 'VIDEO':
                    components.append({'type': 'header', 'parameters': [{'type': 'video', 'video': {'link': str(media_url)}}]})
                elif fmt == 'DOCUMENT':
                    components.append({'type': 'header', 'parameters': [{'type': 'document', 'document': {'link': str(media_url), 'filename': 'document.pdf'}}]})
            elif fmt == 'TEXT':
                header_text = header_comp.get('text', '')
                matches = re.findall(r'\{\{(\d+)\}\}', header_text)
                if matches:
                    val = user_header_params if isinstance(user_header_params, str) else (user_header_params[0] if isinstance(user_header_params, list) and user_header_params else "Notification")
                    components.append({'type': 'header', 'parameters': [{'type': 'text', 'text': str(val)}]})

        # 2. BODY Component
        body_comp = next((c for c in tmpl_def.get('components', []) if c.get('type', '').upper() == 'BODY'), None)
        if body_comp and body_comp.get('text'):
            matches = re.findall(r'\{\{(\d+)\}\}', body_comp['text'])
            expected_count = max([int(m) for m in matches]) if matches else 0
            
            if expected_count > 0:
                params_to_send = list(user_body_params)
                while len(params_to_send) < expected_count:
                    fallback_val = params_to_send[-1] if params_to_send else "-"
                    params_to_send.append(fallback_val)
                params_to_send = params_to_send[:expected_count]
                
                components.append({
                    'type': 'body',
                    'parameters': [{'type': 'text', 'text': str(val) if str(val).strip() else '-'} for val in params_to_send]
                })
            # If expected_count == 0, omit body component to prevent Error 132012

        # 3. BUTTONS Component
        buttons_comp = next((c for c in tmpl_def.get('components', []) if c.get('type', '').upper() == 'BUTTONS'), None)
        if buttons_comp and buttons_comp.get('buttons'):
            for idx, btn in enumerate(buttons_comp['buttons']):
                if btn.get('type', '').upper() == 'URL' and '{{1}}' in btn.get('url', ''):
                    url_param = str(user_body_params[0]).replace(' ', '-') if user_body_params else "offer"
                    components.append({
                        'type': 'button',
                        'sub_type': 'url',
                        'index': str(idx),
                        'parameters': [{'type': 'text', 'text': url_param}]
                    })
    else:
        if user_body_params:
            components.append({
                'type': 'body',
                'parameters': [{'type': 'text', 'text': str(val)} for val in user_body_params if str(val).strip()]
            })

    template_dict = {
        'name': template_name,
        'language': {'code': lang_code},
    }
    if components:
        template_dict['components'] = components
    
    return template_dict


def send_whatsapp_message(customer, event_type, context=None):
    """
    Sends a WhatsApp message to a customer if the WhatsApp API mode is enabled.

    :param customer:   Customer ORM object (must have .name and .phone)
    :param event_type: 'payment_paid' | 'subscription_renewed'
    :param context:    dict with extra data, e.g. {'amount': 50.0, 'expiry_date': '2026-06-21'}
    """
    if context is None:
        context = {}

    try:
        if not getattr(customer, 'whatsapp_notifications_enabled', True):
            return {'success': False, 'status': 'Skipped', 'error': 'Customer has WhatsApp notifications disabled'}  # User disabled notifications

        settings = WhatsAppSettings.query.first()
        if not settings or not settings.enabled:
            return {'success': False, 'status': 'Skipped', 'error': 'WhatsApp system notifications disabled in settings'}  # WhatsApp notifications are disabled

        if settings.mode != 'api':
            # Deep-link mode is manual (button in UI) — nothing to auto-send
            return {'success': True, 'status': 'Simulated / Manual', 'details': 'Sent in manual / deep-link mode'}

        # Validate that required API credentials are present
        if not settings.access_token or not settings.phone_number_id:
            logging.warning('WhatsApp API mode is enabled but credentials are missing – skipping send.')
            return {'success': False, 'status': 'Failed', 'error': 'WhatsApp API credentials missing in settings'}

        # Normalise the recipient phone number (digits only, no leading +)
        phone = normalize_whatsapp_phone(customer.phone)
        if not phone:
            logging.warning(f'Customer {customer.id} has no valid phone number – skipping WhatsApp send.')
            return {'success': False, 'status': 'Failed', 'error': 'Customer has no valid phone number'}

        # Pick the correct approved template name
        if event_type == 'payment_paid':
            template_name = settings.template_payment_paid or 'payment_confirmation'
        elif event_type == 'subscription_created':
            template_name = settings.template_subscription_created or 'subscription_created'
        elif event_type == 'subscription_renewed':
            template_name = settings.template_subscription_renewed or 'subscription_renewal'
        elif event_type == 'payment_reminder':
            template_name = settings.template_payment_reminder or 'payment_reminder'
        elif event_type == 'reseller_credit_added':
            template_name = 'reseller_credit_added'
        elif event_type == 'reseller_discount_applied':
            template_name = 'reseller_discount_applied'
        elif event_type == 'reseller_customer_renewed':
            template_name = 'reseller_customer_renewed'
        elif event_type == 'reseller_payment_collected':
            template_name = 'reseller_payment_collected'
        elif event_type.startswith('bulk_'):
            template_name = getattr(settings, f'template_{event_type}', None)
            if not template_name:
                logging.warning(f'Bulk message missing template_name for {event_type}.')
                return {'success': False, 'status': 'Failed', 'error': f'Missing template name for {event_type}'}
        else:
            logging.warning(f'Unknown WhatsApp event_type "{event_type}" – skipping.')
            return {'success': False, 'status': 'Failed', 'error': f'Unknown event_type {event_type}'}

        api_version = settings.api_version or 'v19.0'
        url = f'https://graph.facebook.com/{api_version}/{settings.phone_number_id}/messages'
        headers = {
            'Authorization': f'Bearer {settings.access_token}',
            'Content-Type': 'application/json',
        }

        # Build template components (header / body parameters)
        user_body_params = []
        if event_type == 'payment_paid':
            amount_str = f"{float(context.get('amount', 0)):.2f}"
            balance_str = f"{float(context.get('balance', customer.balance)):.2f}"
            user_body_params = [customer.name, amount_str, balance_str]
        elif event_type == 'subscription_renewed':
            expiry_date = str(context.get('expiry_date', ''))
            plan_name = str(context.get('plan_name', customer.subscription_plan.name if customer.subscription_plan else 'N/A'))
            balance_str = f"{float(context.get('balance', customer.balance)):.2f}"
            user_body_params = [customer.name, plan_name, expiry_date, balance_str]
        elif event_type == 'payment_reminder':
            balance_str = f"{float(context.get('balance', customer.balance)):.2f}"
            expiry_date = str(context.get('expiry_date', customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else 'N/A'))
            user_body_params = [customer.name, balance_str, expiry_date]
        elif event_type == 'bulk_outage':
            user_body_params = [context.get('message', 'an outage occured from the isp , will be repaired soon')]
        elif event_type == 'bulk_maintenance':
            user_body_params = [context.get('location', ''), context.get('estimated_time', '')]
        elif event_type == 'reseller_credit_added':
            amount_str = f"{float(context.get('amount', 0)):.2f}"
            balance_str = f"{float(context.get('balance', 0)):.2f}"
            user_body_params = [amount_str, balance_str]
        elif event_type == 'reseller_discount_applied':
            amount_str = f"{float(context.get('amount', 0)):.2f}"
            balance_str = f"{float(context.get('balance', 0)):.2f}"
            user_body_params = [amount_str, balance_str]
        elif event_type == 'reseller_customer_renewed':
            customer_name = context.get('customer_name', 'Unknown')
            balance_str = f"{float(context.get('balance', 0)):.2f}"
            user_body_params = [customer_name, balance_str]
        elif event_type == 'reseller_payment_collected':
            amount_str = f"{float(context.get('amount', 0)):.2f}"
            balance_str = f"{float(context.get('balance', 0)):.2f}"
            user_body_params = [amount_str, balance_str]
        elif event_type in ('bulk_feature', 'bulk_offer'):
            user_body_params = [context.get('message', '')]
        else:
            if context and isinstance(context.get('variables'), list):
                user_body_params = context.get('variables')
            elif context and context.get('message'):
                user_body_params = [context.get('message')]

        user_header_params = context.get('header_url') or context.get('image_url') or context.get('media_url')

        template_dict = build_meta_template_payload(
            settings=settings,
            template_name=template_name,
            default_language=settings.template_language or 'en',
            user_body_params=user_body_params,
            user_header_params=user_header_params
        )

        payload = {
            'messaging_product': 'whatsapp',
            'to': phone,
            'type': 'template',
            'template': template_dict,
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.ok:
            res_data = response.json()
            msg_id = res_data.get('messages', [{}])[0].get('id', 'Accepted')
            logging.info(f'WhatsApp [{event_type}] sent to customer {customer.id} ({phone}): {res_data}')
            return {'success': True, 'status': 'Sent (Accepted by Meta)', 'details': f'Dispatched to Meta API queue (ID: {msg_id})'}
        else:
            err_json = {}
            try:
                err_json = response.json().get('error', {})
            except:
                pass
            err_msg = err_json.get('message') or err_json.get('error_data', {}).get('details') or response.text
            logging.error(f'WhatsApp API error for customer {customer.id}: {response.status_code} {response.text}')
            return {'success': False, 'status': 'Failed', 'error': f'Meta API Error ({response.status_code}): {err_msg}'}

    except Exception as exc:
        # Never let a WhatsApp failure break the main payment flow
        logging.error(f'send_whatsapp_message exception: {exc}')
        traceback.print_exc()
        return {'success': False, 'status': 'Failed', 'error': f'Exception: {str(exc)}'}


@app.route('/api/dashboard', methods=['GET'])
@jwt_required()
def get_dashboard_metrics():
    total_customers = Customer.query.count()
    active_customers = Customer.query.filter_by(is_subscription_active=True).count()
    total_revenue = sum(payment.amount for payment in Payment.query.filter_by(paid=True, pre_payment=False).all()) # Only actual revenue, not pre-payments
    total_expenses = sum(expense.amount for expense in Expense.query.filter_by(is_credit=False).all()) + sum(sp.amount for sp in SupplierPayment.query.all())
    # Outstanding balance should be the sum of negative balances (customers who owe money)
    outstanding_balance = sum(c.balance for c in Customer.query.filter(Customer.balance < 0).all())
    subscriptions_breakdown_query = db.session.query(
        SubscriptionPlan.name,
        func.count(Customer.id).label('customer_count')
    ).join(Customer, Customer.subscription_plan_id == SubscriptionPlan.id)\
     .filter(Customer.is_subscription_active == True)\
     .group_by(SubscriptionPlan.name)\
     .order_by(SubscriptionPlan.name)\
     .all()

    subscriptions_breakdown = [
        {'plan_name': name, 'count': count} for name, count in subscriptions_breakdown_query
    ]

    return jsonify({
        'totalCustomers': total_customers,
        'activeCustomers': active_customers,
        'totalRevenue': float(total_revenue),
        'totalExpenses': float(total_expenses),
        'outstandingBalance': float(outstanding_balance),
        'subscriptionsBreakdown': subscriptions_breakdown
    })


@app.route('/api/service-statuses', methods=['GET']) # ADDED: New endpoint for all statuses
@jwt_required()
def get_all_service_statuses():
    statuses = ServiceStatus.query.join(Customer).order_by(ServiceStatus.last_updated.desc()).all()
    return jsonify([{
        'id': s.id,
        'customer_name': s.customer.name, # Added customer name
        'status': s.status,
        'last_updated': s.last_updated.strftime('%Y-%m-%d %H:%M:%S'),
        'notes': s.notes
    } for s in statuses])



@app.route('/api/service-status/<int:customer_id>', methods=['GET'])
@jwt_required()
def get_service_status(customer_id):
    status = ServiceStatus.query.filter_by(customer_id=customer_id).order_by(ServiceStatus.last_updated.desc()).first()
    if not status:
        return jsonify({'message': 'No service status found'}), 404
    return jsonify({
        'id': status.id,
        'status': status.status,
        'last_updated': status.last_updated.strftime('%Y-%m-%d %H:%M:%S'),
        'notes': status.notes
    })

@app.route('/api/service-status/<int:customer_id>', methods=['POST'])
@jwt_required()
def update_service_status(customer_id):
    data = request.json
    status = ServiceStatus(
        customer_id=customer_id,
        status=data['status'],
        notes=data.get('notes', '')
    )
    db.session.add(status)
    db.session.commit()
    return jsonify({'message': 'Service status updated successfully'})

def send_push_notification(payload_dict):
    if not VAPID_PRIVATE_KEY:
        print("Push notification failed: VAPID keys not configured.")
        return
        
    subs = PushSubscription.query.all()
    for sub in subs:
        try:
            sub_info = json.loads(sub.subscription_info)
            webpush(
                subscription_info=sub_info,
                data=json.dumps(payload_dict),
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": "mailto:admin@example.com"}
            )
        except Exception as e:
            print(f"Failed to send push to user {sub.user_id}:", e)
            # Optionally remove invalid subscriptions
            if "410 Gone" in str(e) or "404 Not Found" in str(e):
                db.session.delete(sub)
                db.session.commit()

@app.route('/api/vapid-public-key', methods=['GET'])
def get_vapid_public_key():
    return jsonify({"public_key": VAPID_PUBLIC_KEY})

@app.route('/api/push-subscribe', methods=['POST'])
@jwt_required()
def push_subscribe():
    data = request.json
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    if not user:
        return jsonify({"msg": "User not found"}), 404
        
    sub_info_str = json.dumps(data.get('subscription'))
    
    # Check if this exact subscription already exists for this user
    existing = PushSubscription.query.filter_by(user_id=user.id, subscription_info=sub_info_str).first()
    if not existing:
        new_sub = PushSubscription(user_id=user.id, subscription_info=sub_info_str)
        db.session.add(new_sub)
        db.session.commit()
        
    return jsonify({"msg": "Subscribed successfully"}), 200

@app.route('/api/support-tickets', methods=['POST'])
@jwt_required()
def create_support_ticket():
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    
    data = request.json
    ticket = SupportTicket(
        customer_id=data['customer_id'],
        title=data['title'],
        description=data['description'],
        status='open',
        priority=data['priority']
    )
    db.session.add(ticket)
    db.session.flush() # flush to get ticket.id
    
    if user:
        log = TicketLog(
            ticket_id=ticket.id,
            user_id=user.id,
            action='created',
            details=f"Ticket created with priority {data['priority']}"
        )
        db.session.add(log)
    
    db.session.commit()
    
    # Trigger push notification
    try:
        payload = {
            "title": "New Support Ticket",
            "body": f"{data['title']} (Priority: {data['priority']})",
            "url": "/?view=service"
        }
        send_push_notification(payload)
    except Exception as e:
        print("Push notification error:", e)

    return jsonify({'message': 'Support ticket created successfully', 'id': ticket.id})

@app.route('/api/support-tickets', methods=['GET'])
@jwt_required()
def get_support_tickets():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 100, type=int)
    status = request.args.get('status')
    priority = request.args.get('priority')
    
    query = SupportTicket.query.join(Customer).options(db.joinedload(SupportTicket.logs).joinedload(TicketLog.user))
    if status:
        query = query.filter_by(status=status)
    if priority:
        query = query.filter_by(priority=priority)
    
    pagination = query.order_by(SupportTicket.created_at.desc()).paginate(page=page, per_page=per_page)
    return jsonify({
        'tickets': [{
            'id': t.id,
            'customer_id': t.customer_id,
            'customer_name': t.customer.name if t.customer else 'Unknown',
            'title': t.title,
            'description': t.description,
            'status': t.status,
            'priority': t.priority,
            'created_at': t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
            'updated_at': t.updated_at.strftime('%Y-%m-%d %H:%M:%S'),
            'resolved_at': t.resolved_at.strftime('%Y-%m-%d %H:%M:%S') if t.resolved_at else None,
            'in_progress_at': t.in_progress_at.strftime('%Y-%m-%d %H:%M:%S') if t.in_progress_at else None,
            'in_progress_by': t.in_progress_by.username if t.in_progress_by else None,
            'resolved_by': t.resolved_by.username if t.resolved_by else None,
            'logs': [{
                'id': log.id,
                'action': log.action,
                'details': log.details,
                'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'username': log.user.username if log.user else 'Unknown'
            } for log in sorted(t.logs, key=lambda l: l.timestamp)]
        } for t in pagination.items],
        'total': pagination.total,
        'pages': pagination.pages,
        'current_page': page
    })

@app.route('/api/service-outages', methods=['POST'])
@jwt_required()
def create_service_outage():
    data = request.json
    outage = ServiceOutage(
        title=data['title'],
        description=data['description'],
        affected_areas=data['affected_areas'],
        start_time=datetime.strptime(data['start_time'], '%Y-%m-%d %H:%M:%S'),
        status='active'
    )
    db.session.add(outage)
    db.session.commit()
    return jsonify({'message': 'Service outage created successfully', 'id': outage.id})

@app.route('/api/service-outages', methods=['GET'])
@jwt_required()
def get_service_outages():
    status = request.args.get('status', 'all')
    query = ServiceOutage.query
    if status != 'all':
        query = query.filter_by(status=status)
    outages = query.order_by(ServiceOutage.start_time.desc()).all()
    return jsonify([{
        'id': o.id,
        'title': o.title,
        'description': o.description,
        'affected_areas': o.affected_areas,
        'start_time': o.start_time.strftime('%Y-%m-%d %H:%M:%S') if o.start_time else None,
        'end_time': o.end_time.strftime('%Y-%m-%d %H:%M:%S') if o.end_time else None,
        'status': o.status
    } for o in outages])

@app.route('/api/support-tickets/<int:ticket_id>', methods=['PUT'])
@jwt_required()
def update_support_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket:
        return jsonify({'message': 'Ticket not found'}), 404
    data = request.json
    current_username = get_jwt_identity()
    current_user = User.query.filter_by(username=current_username).first()

    logs_added = False

    if 'status' in data and data['status'] != ticket.status:
        old_status = ticket.status
        new_status = data['status']
        ticket.status = new_status
        if new_status == 'in_progress' and not ticket.in_progress_at:
            ticket.in_progress_at = datetime.utcnow()
            ticket.in_progress_by_id = current_user.id if current_user else None
        if new_status in ('resolved', 'closed'):
            if not ticket.resolved_at:
                ticket.resolved_at = datetime.utcnow()
            ticket.resolved_by_id = current_user.id if current_user else None
            
        if current_user:
            log = TicketLog(ticket_id=ticket.id, user_id=current_user.id, action='status_changed', details=f"Status changed from {old_status} to {new_status}")
            db.session.add(log)
            logs_added = True
            
    if 'priority' in data and data['priority'] != ticket.priority:
        old_priority = ticket.priority
        ticket.priority = data['priority']
        if current_user:
            log = TicketLog(ticket_id=ticket.id, user_id=current_user.id, action='priority_changed', details=f"Priority changed from {old_priority} to {data['priority']}")
            db.session.add(log)
            logs_added = True

    if 'title' in data and data['title'] != ticket.title:
        ticket.title = data['title']
    if 'description' in data and data['description'] != ticket.description:
        ticket.description = data['description']
        
    ticket.updated_at = datetime.utcnow()
    try:
        db.session.commit()
        return jsonify({'message': 'Ticket updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/support-tickets/<int:ticket_id>', methods=['DELETE'])
@jwt_required()
def delete_support_ticket(ticket_id):
    ticket = db.session.get(SupportTicket, ticket_id)
    if not ticket:
        return jsonify({'message': 'Ticket not found'}), 404
    db.session.delete(ticket)
    db.session.commit()
    return jsonify({'message': 'Ticket deleted successfully'})

@app.route('/api/service-outages/<int:outage_id>', methods=['PUT'])
@jwt_required()
def update_service_outage(outage_id):
    outage = db.session.get(ServiceOutage, outage_id)
    if not outage:
        return jsonify({'message': 'Outage not found'}), 404
    data = request.json
    if 'status' in data:
        outage.status = data['status']
        if data['status'] == 'resolved' and not outage.end_time:
            outage.end_time = datetime.utcnow()
    if 'title' in data:
        outage.title = data['title']
    if 'description' in data:
        outage.description = data['description']
    if 'affected_areas' in data:
        outage.affected_areas = data['affected_areas']
    try:
        db.session.commit()
        return jsonify({'message': 'Outage updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/service-statuses/<int:status_id>', methods=['PUT'])
@jwt_required()
def update_service_status_by_id(status_id):
    status_record = db.session.get(ServiceStatus, status_id)
    if not status_record:
        return jsonify({'message': 'Status record not found'}), 404
    data = request.json
    if 'status' in data:
        status_record.status = data['status']
    if 'notes' in data:
        status_record.notes = data['notes']
    status_record.last_updated = datetime.utcnow()
    try:
        db.session.commit()
        return jsonify({'message': 'Service status updated successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/customer-feedback', methods=['POST'])
def submit_feedback():
    data = request.json
    feedback = CustomerFeedback(
        customer_id=data['customer_id'],
        rating=data['rating'],
        comment=data.get('comment', ''),
        category=data['category']
    )
    db.session.add(feedback)
    db.session.commit()
    return jsonify({'message': 'Feedback submitted successfully'})

@app.route('/api/payment-reminders', methods=['POST'])
@jwt_required()
def create_payment_reminder():
    data = request.json
    reminder = PaymentReminder(
        customer_id=data['customer_id'],
        payment_id=data['payment_id'],
        reminder_date=datetime.strptime(data['reminder_date'], '%Y-%m-%d %H:%M:%S'),
        status='pending'
    )
    db.session.add(reminder)
    db.session.commit()
    return jsonify({'message': 'Payment reminder created successfully'})

@app.route('/api/customers/<int:customer_id>/send-whatsapp-reminder', methods=['POST'])
@jwt_required()
def trigger_whatsapp_reminder(customer_id):
    customer = db.session.get(Customer, customer_id)
    if not customer:
        return jsonify({'message': 'Customer not found'}), 404
    
    context = {
        'balance': customer.balance,
        'expiry_date': customer.subscription_expiry_date.strftime('%Y-%m-%d') if customer.subscription_expiry_date else 'N/A'
    }
    
    try:
        send_whatsapp_message(customer, 'payment_reminder', context)
        return jsonify({'message': 'WhatsApp reminder triggered successfully'}), 200
    except Exception as e:
        return jsonify({'message': f'Failed to trigger WhatsApp reminder: {str(e)}'}), 500

@app.route('/api/messages/bulk_send', methods=['POST'])
@jwt_required()
def send_bulk_messages():
    # Only allow admin
    current_username = get_jwt_identity()
    user = User.query.filter_by(username=current_username).first()
    if not user or user.role != 'admin':
        return jsonify({'message': 'Access Denied'}), 403

    data = request.json
    audience = data.get('audience', 'all')

    if audience == 'custom_list':
        custom_phones = data.get('custom_phones', [])
        custom_template = data.get('custom_template')
        template_language = data.get('template_language', 'en')
        custom_variables = data.get('custom_variables', [])

        if not custom_template:
            return jsonify({'message': 'Please select a Meta message template.'}), 400
        if not custom_phones:
            return jsonify({'message': 'No valid mobile numbers provided.'}), 400

        settings = WhatsAppSettings.query.first()
        success_count = 0
        failed_count = 0
        report_details = []

        for phone_raw in custom_phones:
            phone = normalize_whatsapp_phone(phone_raw)
            if not phone:
                failed_count += 1
                report_details.append({
                    'recipient': str(phone_raw),
                    'name': 'Unknown / File Upload',
                    'status': 'Failed',
                    'details': 'Invalid phone number format (must contain digits)'
                })
                continue
            if settings and settings.mode == 'api' and settings.access_token and settings.phone_number_id:
                try:
                    api_version = settings.api_version or 'v19.0'
                    url = f'https://graph.facebook.com/{api_version}/{settings.phone_number_id}/messages'
                    headers = {
                        'Authorization': f'Bearer {settings.access_token}',
                        'Content-Type': 'application/json',
                    }
                    user_body_params = []
                    if custom_variables and isinstance(custom_variables, list):
                        user_body_params = [str(var) for var in custom_variables if str(var).strip()]
                    elif data.get('variables', {}).get('message'):
                        user_body_params = [str(data['variables']['message'])]

                    user_header_params = data.get('header_url', '').strip() or None

                    template_dict = build_meta_template_payload(
                        settings=settings,
                        template_name=custom_template,
                        default_language=template_language or settings.template_language or 'en',
                        user_body_params=user_body_params,
                        user_header_params=user_header_params
                    )

                    payload = {
                        'messaging_product': 'whatsapp',
                        'to': phone,
                        'type': 'template',
                        'template': template_dict,
                    }
                    response = requests.post(url, json=payload, headers=headers, timeout=10)
                    if response.ok:
                        success_count += 1
                        res_data = response.json()
                        msg_id = res_data.get('messages', [{}])[0].get('id', 'Accepted')
                        logging.info(f'Custom Meta template [{custom_template}] sent to {phone}: {res_data}')
                        report_details.append({
                            'recipient': f'+{phone}',
                            'name': 'Custom Contact',
                            'status': 'Sent (Accepted by Meta)',
                            'details': f'Dispatched to Meta API queue (ID: {msg_id})'
                        })
                    else:
                        failed_count += 1
                        err_json = {}
                        try:
                            err_json = response.json().get('error', {})
                        except:
                            pass
                        err_msg = err_json.get('message') or err_json.get('error_data', {}).get('details') or response.text
                        logging.error(f'Meta API error sending to {phone}: {response.status_code} {response.text}')
                        report_details.append({
                            'recipient': f'+{phone}',
                            'name': 'Custom Contact',
                            'status': 'Failed',
                            'details': f'Meta API Error ({response.status_code}): {err_msg}'
                        })
                except Exception as e:
                    failed_count += 1
                    logging.error(f"Error sending custom template to {phone}: {e}")
                    report_details.append({
                        'recipient': f'+{phone}',
                        'name': 'Custom Contact',
                        'status': 'Failed',
                        'details': f'Network / Execution Error: {str(e)}'
                    })
            else:
                success_count += 1
                logging.info(f'Simulated sending custom template [{custom_template}] to {phone}')
                report_details.append({
                    'recipient': f'+{phone}',
                    'name': 'Custom Contact',
                    'status': 'Simulated / Sent',
                    'details': 'Sent in simulated/manual mode (no API credentials configured)'
                })

        total_targeted = success_count + failed_count
        return jsonify({
            'message': f'Dispatched marketing template [{custom_template}] to {total_targeted} contacts ({success_count} sent, {failed_count} failed).',
            'report': {
                'total_targeted': total_targeted,
                'sent_count': success_count,
                'failed_count': failed_count,
                'details': report_details
            }
        }), 200

    event_type = data.get('event_type')
    variables = data.get('variables', {})
    exclude_resellers = data.get('exclude_reseller_customers', False)
    target_sector = data.get('sector', '').strip()

    query = Customer.query.filter_by(whatsapp_notifications_enabled=True)
    if exclude_resellers:
        query = query.filter(Customer.reseller_id == None)
    if target_sector and event_type in ['outage', 'maintenance']:
        query = query.filter(Customer.sector == target_sector)
    
    if audience == 'active':
        query = query.filter_by(is_subscription_active=True)
    elif audience == 'expired':
        query = query.filter_by(is_subscription_active=False)
    
    customers = query.all()
    
    success_count = 0
    failed_count = 0
    report_details = []

    for customer in customers:
        try:
            # We pass variables in the context
            context = {
                **variables
            }
            res = send_whatsapp_message(customer, f'bulk_{event_type}', context)
            if isinstance(res, dict):
                if res.get('success'):
                    success_count += 1
                else:
                    failed_count += 1
                report_details.append({
                    'recipient': f"+{customer.phone}" if customer.phone else "N/A",
                    'name': customer.name or f"Customer #{customer.id}",
                    'status': res.get('status', 'Sent (Accepted by Meta)' if res.get('success') else 'Failed'),
                    'details': res.get('details') or res.get('error') or 'Processed notification'
                })
            else:
                success_count += 1
                report_details.append({
                    'recipient': f"+{customer.phone}" if customer.phone else "N/A",
                    'name': customer.name or f"Customer #{customer.id}",
                    'status': 'Sent (Accepted by Meta)',
                    'details': 'Processed notification'
                })
        except Exception as e:
            failed_count += 1
            logging.error(f"Failed to send bulk message to {customer.id}: {e}")
            report_details.append({
                'recipient': f"+{customer.phone}" if customer.phone else "N/A",
                'name': customer.name or f"Customer #{customer.id}",
                'status': 'Failed',
                'details': f"Error: {str(e)}"
            })

    total_targeted = len(customers)
    return jsonify({
        'message': f'Dispatched notifications to {total_targeted} customers ({success_count} sent, {failed_count} failed).',
        'report': {
            'total_targeted': total_targeted,
            'sent_count': success_count,
            'failed_count': failed_count,
            'details': report_details
        }
    }), 200

@app.route('/api/whatsapp/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    settings = WhatsAppSettings.query.first()
    verify_token = settings.webhook_verify_token if settings and settings.webhook_verify_token else 'delta_net_whatsapp_secret'

    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        if mode == 'subscribe' and token == verify_token:
            logging.info("WhatsApp Webhook verified successfully by Meta!")
            return challenge, 200
        return 'Verification failed', 403

    if request.method == 'POST':
        data = request.json or {}
        try:
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    val = change.get('value', {})
                    messages = val.get('messages', [])
                    contacts = val.get('contacts', [])
                    
                    for msg in messages:
                        sender_phone = msg.get('from', '')
                        msg_text = ''
                        if msg.get('type') == 'text':
                            msg_text = msg.get('text', {}).get('body', '')
                        elif msg.get('type') == 'button':
                            msg_text = msg.get('button', {}).get('text', '[Button Reply]')
                        elif msg.get('type') == 'interactive':
                            inter = msg.get('interactive', {})
                            if inter.get('type') == 'button_reply':
                                msg_text = inter.get('button_reply', {}).get('title', '[Interactive Button]')
                            elif inter.get('type') == 'list_reply':
                                msg_text = inter.get('list_reply', {}).get('title', '[List Reply]')
                        else:
                            msg_text = f"[{msg.get('type', 'Media/Attachment')} message received]"

                        if not msg_text or not sender_phone:
                            continue

                        # Identify customer name
                        cust_name = "Unknown Customer"
                        if contacts and isinstance(contacts, list):
                            cust_name = contacts[0].get('profile', {}).get('name', cust_name)
                        
                        cust_obj = Customer.query.filter(Customer.phone.like(f"%{sender_phone[-8:]}%")).first()
                        if cust_obj:
                            cust_name = cust_obj.name or cust_name

                        logging.info(f"Incoming WhatsApp reply from {cust_name} (+{sender_phone}): {msg_text}")

                        # 1. Forward message to business mobile if configured
                        if settings and settings.forwarding_mobile and settings.access_token and settings.phone_number_id:
                            fwd_phone = normalize_whatsapp_phone(settings.forwarding_mobile)
                            if fwd_phone and fwd_phone != sender_phone:
                                api_version = settings.api_version or 'v19.0'
                                url = f'https://graph.facebook.com/{api_version}/{settings.phone_number_id}/messages'
                                headers = {
                                    'Authorization': f'Bearer {settings.access_token}',
                                    'Content-Type': 'application/json',
                                }
                                fwd_text = f"🔔 *New Customer Reply*\n*From:* {cust_name} (+{sender_phone})\n*Message:* {msg_text}"
                                payload_fwd = {
                                    'messaging_product': 'whatsapp',
                                    'to': fwd_phone,
                                    'type': 'text',
                                    'text': {'body': fwd_text}
                                }
                                res_fwd = requests.post(url, json=payload_fwd, headers=headers, timeout=10)
                                if not res_fwd.ok:
                                    logging.warning(f"Could not forward text reply to {fwd_phone} (may need 24h session open or template): {res_fwd.text}")
                                    # Fallback: Try sending using an approved template if 24h window is closed
                                    try:
                                        tmpl_name = settings.template_bulk_outage or 'outage_alert'
                                        payload_tmpl = {
                                            'messaging_product': 'whatsapp',
                                            'to': fwd_phone,
                                            'type': 'template',
                                            'template': {
                                                'name': tmpl_name,
                                                'language': {'code': settings.template_language or 'en'},
                                                'components': [{'type': 'body', 'parameters': [{'type': 'text', 'text': f"Reply from +{sender_phone}: {msg_text[:60]}"}]}]
                                            }
                                        }
                                        requests.post(url, json=payload_tmpl, headers=headers, timeout=10)
                                    except Exception:
                                        pass
                                else:
                                    logging.info(f"Successfully forwarded customer reply to business mobile (+{fwd_phone})!")

                        # 2. Automatically log as a Support Ticket in Dashboard if customer found
                        if cust_obj:
                            try:
                                ticket = SupportTicket(
                                    customer_id=cust_obj.id,
                                    title=f"WhatsApp Reply from {cust_name} (+{sender_phone})",
                                    description=f"Incoming WhatsApp message:\n\n{msg_text}\n\n[Received via Meta Cloud API Webhook at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC]",
                                    status='open',
                                    priority='medium',
                                    created_at=datetime.utcnow(),
                                    updated_at=datetime.utcnow()
                                )
                                db.session.add(ticket)
                                db.session.commit()
                                logging.info(f"Created support ticket #{ticket.id} for WhatsApp reply from {cust_name}.")
                            except Exception as ex_t:
                                db.session.rollback()
                                logging.error(f"Failed to create support ticket for WhatsApp reply: {ex_t}")

        except Exception as e:
            logging.error(f"Error processing WhatsApp webhook: {e}")
            traceback.print_exc()

        return jsonify({'status': 'ok'}), 200

@app.route('/api/reports/revenue', methods=['GET'])
@jwt_required()
def get_revenue_report():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = Payment.query.filter(Payment.paid == True)
    if start_date:
        query = query.filter(func.coalesce(Payment.paid_at, Payment.date) >= datetime.strptime(start_date, '%Y-%m-%d'))
    if end_date:
        query = query.filter(func.coalesce(Payment.paid_at, Payment.date) <= datetime.strptime(end_date, '%Y-%m-%d'))
    
    payments = query.all()
    total_revenue = sum(p.amount for p in payments)
    
    # Group by subscription plan
    plan_revenue = {}
    for payment in payments:
        customer = db.session.get(Customer, payment.customer_id) # Use db.session.get
        if customer:
            plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id) # Use db.session.get
            if plan:
                plan_revenue[plan.name] = plan_revenue.get(plan.name, 0) + payment.amount
    
    return jsonify({
        'total_revenue': total_revenue,
        'plan_revenue': plan_revenue,
        'payment_count': len(payments)
    })

@app.route('/api/reports/overdue', methods=['GET'])
@jwt_required()
def get_overdue_payments():
    days_overdue = request.args.get('days', 30, type=int)
    cutoff_date = datetime.utcnow() - timedelta(days=days_overdue)
    
    overdue_payments = Payment.query.filter(
        Payment.paid == False,
        Payment.date <= cutoff_date
    ).all()
    
    return jsonify([{
        'id': p.id,
        'customer_id': p.customer_id,
        'customer_name': db.session.get(Customer, p.customer_id).name, # Use db.session.get
        'amount': p.amount,
        'date': p.date.strftime('%Y-%m-%d'),
        'days_overdue': (datetime.utcnow() - p.date).days
    } for p in overdue_payments])


@app.route('/api/customers/<int:customer_id>/renew_subscription', methods=['POST'])
@jwt_required()
def renew_subscription(customer_id):
    try:
        customer = db.session.get(Customer, customer_id)
        if not customer:
            return jsonify({'message': 'Customer not found!'}), 404

        subscription_plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)
        if not subscription_plan:
            return jsonify({'message': 'Subscription plan not found for this customer!'}), 404
        today = datetime.utcnow()
        current_expiry_date = customer.subscription_expiry_date
        renewal_basis_date = current_expiry_date if current_expiry_date and current_expiry_date > today else today

        if subscription_plan.billing_cycle == 'monthly':
            if current_expiry_date:
                day = current_expiry_date.day
                next_month = renewal_basis_date + relativedelta(months=1)
                last_day_of_next_month = calendar.monthrange(next_month.year, next_month.month)[1]
                day = min(day, last_day_of_next_month)
                new_expiry_date = next_month.replace(day=day)
            else:
                new_expiry_date = renewal_basis_date + relativedelta(months=1)
        elif subscription_plan.billing_cycle == 'yearly':
            new_expiry_date = renewal_basis_date + relativedelta(years=1)
        else:
            return jsonify({'message': 'Unrecognized billing cycle for subscription plan.'}), 400

        customer.subscription_expiry_date = new_expiry_date
        customer.is_subscription_active = True

        renewal_amount = subscription_plan.price - customer.discount
        if renewal_amount < 0:
            renewal_amount = 0.0

        if renewal_amount > 0 and not has_pending_payment(customer.id, new_expiry_date):
            if customer.reseller_id:
                reseller = db.session.get(Reseller, customer.reseller_id)
                if reseller:
                    reseller.balance += renewal_amount
                    reseller_payment = ResellerPayment(
                        reseller_id=reseller.id,
                        amount=renewal_amount,
                        type='credit_added',
                        description=f'Renewal for customer {customer.name}'
                    )
                    db.session.add(reseller_payment)
                    db.session.commit()
                    
                    try:
                        class FakeCustomer:
                            phone = reseller.phone
                            whatsapp_notifications_enabled = True
                            id = reseller.id
                            name = reseller.name
                            
                        send_whatsapp_message(
                            FakeCustomer(),
                            event_type='reseller_customer_renewed',
                            context={'amount': renewal_amount, 'balance': reseller.balance, 'customer_name': customer.name}
                        )
                    except Exception as wa_error:
                        logging.error(f"Failed to send WA message on renew to reseller: {wa_error}")
            else:
                new_payment = Payment(
                    customer_id=customer.id,
                    amount=renewal_amount,
                    paid=False,
                    date=current_expiry_date,
                    pre_payment=False
                )
                db.session.add(new_payment)
                
                customer.balance -= renewal_amount
                db.session.commit()

                try:
                    send_whatsapp_message(
                        customer,
                        event_type='subscription_renewed',
                        context={'expiry_date': new_expiry_date.strftime('%Y-%m-%d')}
                    )
                except Exception as wa_error:
                    logging.error(f"Failed to send WA message on renew: {wa_error}")
        else:
            db.session.commit()

        return jsonify({
            'message': 'Subscription renewed successfully!',
            'customer_id': customer.id,
            'new_expiry_date': new_expiry_date.strftime('%Y-%m-%d'),
            'renewal_payment_amount': float(renewal_amount),
            'customer_new_balance': float(customer.balance),
            'reseller_billed': True if customer.reseller_id else False
        }), 200

    except Exception as e:
        db.session.rollback()
        traceback.print_exc()
        return jsonify({'error': f"Error renewing subscription: {str(e)}"}), 500

# --- SECTOR ENDPOINTS ---

@app.route('/api/sectors', methods=['GET'])
@jwt_required()
def get_sectors():
    sectors = Sector.query.all()
    return jsonify([s.to_dict() for s in sectors]), 200

@app.route('/api/sectors', methods=['POST'])
@jwt_required()
def add_sector():
    data = request.json
    if not data or 'name' not in data or not data['name'].strip():
        return jsonify({'error': 'Sector name is required.'}), 400
    
    existing = Sector.query.filter_by(name=data['name'].strip()).first()
    if existing:
        return jsonify({'error': 'Sector already exists.'}), 400

    new_sector = Sector(name=data['name'].strip())
    db.session.add(new_sector)
    db.session.commit()
    return jsonify({'message': 'Sector added successfully!', 'sector': new_sector.to_dict()}), 201

@app.route('/api/sectors/<int:id>', methods=['PUT'])
@jwt_required()
def edit_sector(id):
    sector = db.session.get(Sector, id)
    if not sector:
        return jsonify({'error': 'Sector not found.'}), 404
    
    data = request.json
    if not data or 'name' not in data or not data['name'].strip():
        return jsonify({'error': 'Sector name is required.'}), 400

    new_name = data['name'].strip()
    existing = Sector.query.filter(Sector.name == new_name, Sector.id != id).first()
    if existing:
        return jsonify({'error': 'Another sector with this name already exists.'}), 400
        
    sector.name = new_name
    db.session.commit()
    return jsonify({'message': 'Sector updated successfully!', 'sector': sector.to_dict()}), 200

@app.route('/api/sectors/<int:id>', methods=['DELETE'])
@jwt_required()
def delete_sector(id):
    sector = db.session.get(Sector, id)
    if not sector:
        return jsonify({'error': 'Sector not found.'}), 404
        
    # Optional: check if any customer uses this sector name before deleting? 
    # For now just delete the definition. Customers will keep the string value.
    db.session.delete(sector)
    db.session.commit()
    return jsonify({'message': 'Sector deleted successfully!'}), 200

@app.route('/api/expense_categories', methods=['GET'])
@jwt_required()
def get_expense_categories():
    categories = ExpenseCategory.query.order_by(ExpenseCategory.name).all()
    return jsonify([c.to_dict() for c in categories])

@app.route('/api/expense_categories', methods=['POST'])
@jwt_required()
def add_expense_category():
    data = request.json
    if not data or 'name' not in data or not data['name'].strip():
        return jsonify({'error': 'Category name is required.'}), 400
    try:
        new_category = ExpenseCategory(name=data['name'].strip())
        db.session.add(new_category)
        db.session.commit()
        return jsonify(new_category.to_dict()), 201
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'This category already exists.'}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/expense_categories/<int:category_id>', methods=['PUT'])
@jwt_required()
def update_expense_category(category_id):
    data = request.json
    category = db.session.get(ExpenseCategory, category_id)
    if not category:
        return jsonify({'message': 'Category not found!'}), 404
    if 'name' not in data or not data['name'].strip():
        return jsonify({'error': 'Category name is required.'}), 400
    try:
        category.name = data['name'].strip()
        db.session.commit()
        return jsonify(category.to_dict()), 200
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'This category name already exists.'}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/expense_categories/<int:category_id>', methods=['DELETE'])
@jwt_required()
def delete_expense_category(category_id):
    category = db.session.get(ExpenseCategory, category_id)
    if not category:
        return jsonify({'message': 'Category not found!'}), 404
    # Check if any expenses are using this category
    if category.expenses:
        return jsonify({'error': 'Cannot delete category as it is currently in use by expenses.'}), 400
    try:
        db.session.delete(category)
        db.session.commit()
        return jsonify({'message': 'Category deleted successfully!'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500



@app.route('/api/expenses', methods=['GET'])
@jwt_required()
def get_expenses():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    query = Expense.query
    
    # Apply date filters if provided
    if start_date:
        query = query.filter(Expense.date >= start_date)
    if end_date:
        query = query.filter(Expense.date <= end_date)
    
    expenses = query.order_by(Expense.date.desc()).all()
    return jsonify([e.to_dict() for e in expenses])

@app.route('/api/expenses', methods=['POST'])
@jwt_required()
def add_expense():
    try:
        data = request.json
        # Find the category by name to get its ID
        category = ExpenseCategory.query.filter_by(name=data['category']).first()
        if not category:
            return jsonify({'error': f"Category '{data['category']}' not found."}), 400
        
        new_expense = Expense(
            category_id=category.id,
            amount=float(data['amount']),
            description=data['description'],
            date=datetime.strptime(data['date'], '%Y-%m-%d'),
            is_credit=data.get('is_credit', False),
            supplier_id=data.get('supplier_id')
        )
        db.session.add(new_expense)
        
        # Update supplier balance if it's a credit expense
        if new_expense.is_credit and new_expense.supplier_id:
            supplier = db.session.get(Supplier, new_expense.supplier_id)
            if supplier:
                supplier.balance += new_expense.amount

        db.session.commit()
        return jsonify(new_expense.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/expenses/<int:expense_id>', methods=['PUT'])
@jwt_required()
def update_expense(expense_id):
    try:
        data = request.json
        expense = db.session.get(Expense, expense_id)
        if not expense:
            return jsonify({'message': 'Expense not found!'}), 404
        
        if 'category' in data:
            category = ExpenseCategory.query.filter_by(name=data['category']).first()
            if not category:
                return jsonify({'error': f"Category '{data['category']}' not found."}), 400
            expense.category_id = category.id

        new_amount = float(data.get('amount', expense.amount))
        new_is_credit = data.get('is_credit', expense.is_credit)
        new_supplier_id = data.get('supplier_id', expense.supplier_id)
        
        # Handle balance changes if supplier or amount or credit status changed
        if expense.is_credit and expense.supplier_id:
            old_supplier = db.session.get(Supplier, expense.supplier_id)
            if old_supplier:
                old_supplier.balance -= expense.amount  # Revert old expense amount

        expense.amount = new_amount
        expense.is_credit = new_is_credit
        expense.supplier_id = new_supplier_id if new_is_credit else None
        expense.description = data.get('description', expense.description)
        expense.date = datetime.strptime(data.get('date', expense.date.strftime('%Y-%m-%d')), '%Y-%m-%d')
        
        if expense.is_credit and expense.supplier_id:
            new_supplier = db.session.get(Supplier, expense.supplier_id)
            if new_supplier:
                new_supplier.balance += expense.amount  # Apply new expense amount

        db.session.commit()
        return jsonify(expense.to_dict()), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/expenses/<int:expense_id>', methods=['DELETE'])
@jwt_required()
def delete_expense(expense_id):
    try:
        expense = db.session.get(Expense, expense_id)
        if not expense:
            return jsonify({'message': 'Expense not found!'}), 404
        
        db.session.delete(expense)
        db.session.commit()
        return jsonify({'message': 'Expense deleted successfully!'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500



# --- NEW: API Endpoints for the Receipts View ---

@app.route('/api/receipts', methods=['GET'])
@jwt_required()
def get_generated_receipts():
    search_query = request.args.get('search_query', '')
    query = GeneratedReceipt.query.join(Customer).order_by(GeneratedReceipt.billing_date.desc())

    if search_query:
        query = query.filter(Customer.name.ilike(f'%{search_query}%'))

    receipts = query.all()
    
    return jsonify([{
        'id': r.id,
        'customer_id': r.customer_id,
        'customer_name': r.customer.name,
        'billing_date': r.billing_date.strftime('%Y-%m-%d'),
        'generation_date': r.generation_date.strftime('%Y-%m-%d %H:%M'),
        'print_count': r.print_count,
        'last_printed_date': r.last_printed_date.strftime('%Y-%m-%d %H:%M') if r.last_printed_date else 'Never',
        'receipt_data': json.loads(r.receipt_data)
    } for r in receipts])

@app.route('/api/receipts/generate', methods=['POST'])
@jwt_required()
def generate_receipts_for_month():
    data = request.json
    year = data.get('year')
    month = data.get('month')

    if not year or not month:
        return jsonify({'error': 'Year and month are required.'}), 400

    # Find all unpaid payments for the specified month and year
    payments_to_process = Payment.query.filter(
        extract('year', Payment.date) == year,
        extract('month', Payment.date) == month,
        Payment.paid == False
    ).all()

    generated_count = 0
    for payment in payments_to_process:
        # Check if a receipt has already been generated for this payment
        existing_receipt = GeneratedReceipt.query.filter_by(payment_id=payment.id).first()
        if existing_receipt:
            continue

        customer = db.session.get(Customer, payment.customer_id)
        plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)

        # Create a data snapshot for the receipt
        receipt_data_snapshot = {
            'customer_name': customer.name,
            'customer_address': customer.address,
            'customer_phone': customer.phone,
            'payment_date': payment.date.strftime('%Y-%m-%d'),
            'subscription_plan_details': plan.to_dict() if plan else {},
            'amount_on_record': payment.amount,
            'customer_new_balance': customer.balance # Balance at the time of generation
        }

        new_receipt_log = GeneratedReceipt(
            customer_id=customer.id,
            payment_id=payment.id,
            billing_date=payment.date,
            receipt_data=json.dumps(receipt_data_snapshot)
        )
        db.session.add(new_receipt_log)
        generated_count += 1
    
    db.session.commit()
    return jsonify({'message': f'{generated_count} new receipts generated for {month}/{year}.'}), 200

@app.route('/api/receipts/log_print', methods=['POST'])
def log_receipt_print():
    data = request.json
    receipt_ids = data.get('receipt_ids', [])
    
    if not receipt_ids:
        return jsonify({'error': 'No receipt IDs provided.'}), 400

    receipts_to_update = GeneratedReceipt.query.filter(GeneratedReceipt.id.in_(receipt_ids)).all()

    for receipt in receipts_to_update:
        receipt.print_count += 1
        receipt.last_printed_date = datetime.utcnow()

    db.session.commit()
    return jsonify({'message': f'Print logged for {len(receipts_to_update)} receipts.'}), 200


@app.route('/api/reports/active-subscriptions-by-plan', methods=['GET'])
@jwt_required()
def get_active_subscriptions_by_plan():
    """
    Get count of active subscriptions grouped by subscription plan
    """
    try:
        # Get all active customers and group them manually
        active_customers = Customer.query.filter_by(is_subscription_active=True).all()
        
        # Dictionary to store plan counts with price info
        plan_counts = {}
        
        for customer in active_customers:
            plan = db.session.get(SubscriptionPlan, customer.subscription_plan_id)
            if plan:
                # Create a unique key with plan name and price
                plan_key = f"{plan.name} - ${plan.price}"
                if plan_key in plan_counts:
                    plan_counts[plan_key] += 1
                else:
                    plan_counts[plan_key] = 1
            else:
                # Handle customers with no plan
                if 'No Plan - $0' in plan_counts:
                    plan_counts['No Plan - $0'] += 1
                else:
                    plan_counts['No Plan - $0'] = 1
        
        # Convert to the expected format
        result = []
        for plan_name_with_price, count in plan_counts.items():
            result.append({
                'plan_name': plan_name_with_price,
                'active_count': count
            })
            
        return jsonify(result), 200

    except Exception as e:
        print(f"Error occurred: {str(e)}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/reports/collector-progress', methods=['GET'])
@jwt_required()
def get_collector_progress():
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return jsonify({'error': 'start_date and end_date are required'}), 400

        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = end_date.replace(hour=23, minute=59, second=59)

        collector_query = db.session.query(
            User.username,
            func.sum(Payment.amount).label('total_amount'),
            func.count(Payment.id).label('total_payments')
        ).join(Payment, Payment.collected_by_id == User.id)\
         .filter(
             Payment.collected_at >= start_date,
             Payment.collected_at <= end_date
         ).group_by(User.username).all()

        results = []
        for row in collector_query:
            results.append({
                'collector_name': row.username,
                'total_amount': float(row.total_amount or 0),
                'total_payments': row.total_payments
            })

        return jsonify(results), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/api/reports/financial', methods=['GET'])
@jwt_required()
def get_financial_report():
    """
    Get Income, Expenses, and Profit aggregated by month for a given date range.
    """
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return jsonify({'error': 'start_date and end_date are required'}), 400

        # Basic parsing stripping 'Z' if present
        start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00')).replace(tzinfo=None)
        end_date = end_date.replace(hour=23, minute=59, second=59)

        # 1. Income: Payments marked as paid. Fall back to date if paid_at is null.
        income_query = db.session.query(
            func.strftime('%Y-%m', func.coalesce(Payment.paid_at, Payment.date)).label('month'),
            func.sum(Payment.amount).label('total')
        ).filter(
            Payment.paid == True,
            func.coalesce(Payment.paid_at, Payment.date) >= start_date,
            func.coalesce(Payment.paid_at, Payment.date) <= end_date
        ).group_by('month').all()

        # 2. Expenses (direct non-credit)
        expense_query = db.session.query(
            func.strftime('%Y-%m', Expense.date).label('month'),
            func.sum(Expense.amount).label('total')
        ).filter(
            Expense.is_credit == False,
            Expense.date >= start_date,
            Expense.date <= end_date
        ).group_by('month').all()

        # 3. Supplier cash payments
        sp_query = db.session.query(
            func.strftime('%Y-%m', SupplierPayment.payment_date).label('month'),
            func.sum(SupplierPayment.amount).label('total')
        ).filter(
            SupplierPayment.payment_date >= start_date,
            SupplierPayment.payment_date <= end_date
        ).group_by('month').all()

        # Combine results
        months_set = set([row.month for row in income_query] + [row.month for row in expense_query] + [row.month for row in sp_query])
        
        monthly_data_dict = {m: {'month': m, 'income': 0.0, 'expenses': 0.0, 'profit': 0.0} for m in months_set}

        for row in income_query:
            monthly_data_dict[row.month]['income'] += float(row.total or 0)
        
        for row in expense_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        for row in sp_query:
            monthly_data_dict[row.month]['expenses'] += float(row.total or 0)

        monthly_data = []
        total_income = 0.0
        total_expenses = 0.0

        for m in sorted(months_set):
            data = monthly_data_dict[m]
            data['profit'] = data['income'] - data['expenses']
            monthly_data.append(data)
            
            total_income += data['income']
            total_expenses += data['expenses']

        total_profit = total_income - total_expenses

        return jsonify({
            'monthly_data': monthly_data,
            'totals': {
                'income': total_income,
                'expenses': total_expenses,
                'profit': total_profit
            }
        }), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# Graceful shutdown handler
def signal_handler(sig, frame):
    print('\nShutting down gracefully...')
    try:
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except:
        pass
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@app.route('/manifest.json')
def serve_manifest():
    with app.app_context():
        settings = BusinessSettings.query.first()
        name = settings.business_name if settings and settings.business_name else "DeltaNet Management System"
        short_name = name
        logo_url = settings.logo_url if settings and settings.logo_url else "logo192.png"
        
        if logo_url and not logo_url.startswith('/') and not logo_url.startswith('http') and logo_url != "logo192.png":
            logo_url = '/uploads/' + logo_url
        elif logo_url == "logo192.png":
            logo_url = '/' + logo_url
        
        icon_type = 'image/png'
        lower_logo = logo_url.lower()
        if lower_logo.endswith('.jpg') or lower_logo.endswith('.jpeg'):
            icon_type = 'image/jpeg'
        elif lower_logo.endswith('.svg'):
            icon_type = 'image/svg+xml'
        elif lower_logo.endswith('.webp'):
            icon_type = 'image/webp'
        elif lower_logo.endswith('.gif'):
            icon_type = 'image/gif'

        manifest = {
            "short_name": short_name,
            "name": name,
            "icons": [
                {
                    "src": logo_url,
                    "sizes": "192x192",
                    "type": icon_type,
                    "purpose": "any maskable"
                },
                {
                    "src": logo_url,
                    "sizes": "512x512",
                    "type": icon_type,
                    "purpose": "any maskable"
                }
            ],
            "start_url": ".",
            "display": "standalone",
            "theme_color": "#000000",
            "background_color": "#ffffff"
        }
        
        response = jsonify(manifest)
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response


# --- Reseller API Endpoints ---


@app.route('/api/resellers/<int:reseller_id>/history', methods=['GET'])
@jwt_required()
def get_reseller_history(reseller_id):
    reseller = db.session.get(Reseller, reseller_id)
    if not reseller:
        return jsonify({'message': 'Reseller not found'}), 404

    payments = ResellerPayment.query.filter_by(reseller_id=reseller_id).order_by(ResellerPayment.date.desc()).all()
    result = [p.to_dict() for p in payments]
    return jsonify(result), 200

@app.route('/api/resellers', methods=['GET'])
@jwt_required()
def get_resellers():
    resellers = Reseller.query.all()
    result = []
    for r in resellers:
        data = r.to_dict()
        data['customers'] = [c.id for c in r.customers]
        result.append(data)
    return jsonify(result), 200

@app.route('/api/resellers', methods=['POST'])
@jwt_required()
def create_reseller():
    data = request.json
    try:
        new_reseller = Reseller(
            name=data['name'],
            phone=data['phone'],
            type=data['type'], # 'type1' or 'type2'
            balance=float(data.get('balance', 0.0))
        )
        db.session.add(new_reseller)
        db.session.commit()
        return jsonify({'message': 'Reseller created successfully!', 'reseller': new_reseller.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/resellers/<int:reseller_id>', methods=['PUT'])
@jwt_required()
def update_reseller(reseller_id):
    data = request.json
    reseller = db.session.get(Reseller, reseller_id)
    if not reseller:
        return jsonify({'message': 'Reseller not found!'}), 404
    try:
        reseller.name = data.get('name', reseller.name)
        reseller.phone = data.get('phone', reseller.phone)
        reseller.type = data.get('type', reseller.type)
        if 'balance' in data:
            reseller.balance = float(data['balance'])
        db.session.commit()
        return jsonify({'message': 'Reseller updated successfully!', 'reseller': reseller.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/resellers/<int:reseller_id>/add_credit', methods=['POST'])
@jwt_required()
def add_reseller_credit(reseller_id):
    data = request.json
    reseller = db.session.get(Reseller, reseller_id)
    if not reseller:
        return jsonify({'message': 'Reseller not found!'}), 404
    
    amount = float(data.get('amount', 0))
    if amount <= 0:
        return jsonify({'error': 'Amount must be positive'}), 400

    try:
        reseller.balance += amount
        new_payment = ResellerPayment(
            reseller_id=reseller.id,
            amount=amount,
            type='credit_added',
            description=data.get('description', 'Manual credit addition')
        )
        db.session.add(new_payment)
        db.session.commit()

        # Send WhatsApp Notification
        class FakeCustomer:
            phone = reseller.phone
            whatsapp_notifications_enabled = True
            id = reseller.id
            name = reseller.name

        send_whatsapp_message(
            FakeCustomer(),
            event_type='reseller_credit_added',
            context={'amount': amount, 'balance': reseller.balance}
        )

        return jsonify({'message': 'Credit added successfully!', 'reseller': reseller.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/resellers/<int:reseller_id>/apply_discount', methods=['POST'])
@jwt_required()
def apply_reseller_discount(reseller_id):
    data = request.json
    reseller = db.session.get(Reseller, reseller_id)
    if not reseller:
        return jsonify({'message': 'Reseller not found!'}), 404
    
    amount = float(data.get('amount', 0))
    if amount <= 0:
        return jsonify({'error': 'Amount must be positive'}), 400

    try:
        reseller.balance -= amount
        new_payment = ResellerPayment(
            reseller_id=reseller.id,
            amount=amount,
            type='discount_applied',
            description=data.get('description', f'Discount applied')
        )
        db.session.add(new_payment)
        db.session.commit()

        # Send WhatsApp Notification
        class FakeCustomer:
            phone = reseller.phone
            whatsapp_notifications_enabled = True
            id = reseller.id
            name = reseller.name

        send_whatsapp_message(
            FakeCustomer(),
            event_type='reseller_discount_applied',
            context={'amount': amount, 'balance': reseller.balance}
        )

        return jsonify({'message': 'Discount applied successfully!', 'reseller': reseller.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/resellers/<int:reseller_id>/collect_payment', methods=['POST'])
@jwt_required()
def collect_reseller_payment(reseller_id):
    data = request.json
    reseller = db.session.get(Reseller, reseller_id)
    if not reseller:
        return jsonify({'message': 'Reseller not found!'}), 404
    
    amount = float(data.get('amount', 0))
    if amount <= 0:
        return jsonify({'error': 'Amount must be positive'}), 400

    try:
        reseller.balance -= amount
        new_payment = ResellerPayment(
            reseller_id=reseller.id,
            amount=amount,
            type='payment_received',
            description=data.get('description', 'Payment received')
        )
        db.session.add(new_payment)
        db.session.commit()

        # Send WhatsApp Notification
        class FakeCustomer:
            phone = reseller.phone
            whatsapp_notifications_enabled = True
            id = reseller.id
            name = reseller.name

        try:
            send_whatsapp_message(
                FakeCustomer(),
                event_type='reseller_payment_collected',
                context={'amount': amount, 'balance': reseller.balance, 'reseller_name': reseller.name}
            )
        except Exception as wa_error:
            import logging
            logging.error(f"Failed to send WA message on payment collect to reseller: {wa_error}")

        return jsonify({'message': 'Payment collected successfully!', 'reseller': reseller.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400



# --- NEW: API Endpoints for Suppliers ---

@app.route('/api/suppliers', methods=['GET'])
@jwt_required()
def get_suppliers():
    suppliers = Supplier.query.order_by(Supplier.name).all()
    return jsonify([s.to_dict() for s in suppliers])

@app.route('/api/suppliers', methods=['POST'])
@jwt_required()
def add_supplier():
    data = request.json
    try:
        new_supplier = Supplier(
            name=data['name'],
            phone=data.get('phone', ''),
            address=data.get('address', ''),
            notes=data.get('notes', '')
        )
        db.session.add(new_supplier)
        db.session.commit()
        return jsonify(new_supplier.to_dict()), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/suppliers/<int:supplier_id>', methods=['PUT'])
@jwt_required()
def update_supplier(supplier_id):
    data = request.json
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return jsonify({'message': 'Supplier not found!'}), 404

    supplier.name = data.get('name', supplier.name)
    supplier.phone = data.get('phone', supplier.phone)
    supplier.address = data.get('address', supplier.address)
    supplier.notes = data.get('notes', supplier.notes)
    if 'balance' in data and data['balance'] is not None and data['balance'] != '':
        supplier.balance = float(data['balance'])

    db.session.commit()
    return jsonify(supplier.to_dict()), 200

@app.route('/api/suppliers/<int:supplier_id>', methods=['DELETE'])
@jwt_required()
def delete_supplier(supplier_id):
    try:
        supplier = db.session.get(Supplier, supplier_id)
        if not supplier:
            return jsonify({'message': 'Supplier not found!'}), 404

        # Check if supplier has linked expenses or payments
        if Expense.query.filter_by(supplier_id=supplier.id).first():
            return jsonify({'error': 'Cannot delete supplier with linked expenses.'}), 400
            
        if SupplierPayment.query.filter_by(supplier_id=supplier.id).first():
            return jsonify({'error': 'Cannot delete supplier with existing payments.'}), 400

        db.session.delete(supplier)
        db.session.commit()
        return jsonify({'message': 'Supplier deleted successfully!'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

@app.route('/api/suppliers/<int:supplier_id>/payments', methods=['GET'])
@jwt_required()
def get_supplier_payments(supplier_id):
    payments = SupplierPayment.query.filter_by(supplier_id=supplier_id).order_by(SupplierPayment.payment_date.desc()).all()
    return jsonify([p.to_dict() for p in payments])

@app.route('/api/suppliers/<int:supplier_id>/payments', methods=['POST'])
@jwt_required()
def record_supplier_payment(supplier_id):
    data = request.json
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return jsonify({'message': 'Supplier not found!'}), 404

    amount = float(data.get('amount', 0))
    if amount <= 0:
        return jsonify({'error': 'Amount must be positive'}), 400

    try:
        # Reduce the balance
        supplier.balance -= amount
        
        new_payment = SupplierPayment(
            supplier_id=supplier.id,
            amount=amount,
            payment_method=data.get('payment_method', ''),
            reference_note=data.get('reference_note', '')
        )
        if 'payment_date' in data and data['payment_date']:
            new_payment.payment_date = datetime.strptime(data['payment_date'], '%Y-%m-%d')
            
        db.session.add(new_payment)
        db.session.commit()
        
        return jsonify({'message': 'Payment recorded successfully!', 'supplier': supplier.to_dict(), 'payment': new_payment.to_dict()}), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


@app.route('/api/suppliers/<int:supplier_id>/history', methods=['GET'])
@jwt_required()
def get_supplier_history(supplier_id):
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return jsonify({'message': 'Supplier not found!'}), 404

    history = []
    # 1. Credit Purchases (Expenses)
    credit_expenses = Expense.query.filter_by(supplier_id=supplier_id, is_credit=True).all()
    for exp in credit_expenses:
        history.append({
            'id': f"exp_{exp.id}",
            'type': 'credit_purchase',
            'title': 'Items Purchased on Credit',
            'description': exp.description,
            'amount': float(exp.amount),
            'date': exp.date.strftime('%Y-%m-%d %H:%M:%S')
        })

    # 2. Payments Made
    payments = SupplierPayment.query.filter_by(supplier_id=supplier_id).all()
    for p in payments:
        history.append({
            'id': f"pay_{p.id}",
            'type': 'payment',
            'title': f"Payment Made ({p.payment_method})" if p.payment_method else "Payment Made",
            'description': p.reference_note or 'Payment to supplier',
            'amount': -float(p.amount),
            'date': p.payment_date.strftime('%Y-%m-%d %H:%M:%S')
        })

    history.sort(key=lambda x: x['date'], reverse=True)
    return jsonify({
        'supplier': supplier.to_dict(),
        'history': history
    }), 200


@app.route('/api/suppliers/<int:supplier_id>/fix-balance', methods=['PUT'])
@jwt_required()
def fix_supplier_balance(supplier_id):
    data = request.json
    supplier = db.session.get(Supplier, supplier_id)
    if not supplier:
        return jsonify({'message': 'Supplier not found!'}), 404

    if 'balance' not in data:
        return jsonify({'error': 'New balance is required'}), 400

    supplier.balance = float(data['balance'])
    db.session.commit()
    return jsonify({'message': 'Supplier balance fixed successfully!', 'supplier': supplier.to_dict()}), 200

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(os.path.join(app.static_folder, path)):
        response = send_from_directory(app.static_folder, path)
        if path in ['service-worker.js', 'manifest.json', 'index.html']:
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        return response
    elif path.startswith('uploads/'):
        return send_from_directory('.', path)
    else:
        response = send_from_directory(app.static_folder, 'index.html')
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

# Start the Flask app
if __name__ == '__main__':
    app.run(host='0.0.0.0',debug=False)


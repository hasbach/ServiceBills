// src/context/AppContext.js
import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

// --- API Service Abstraction ---
// Use Flask backend URL during development, empty for production builds
const API_BASE_URL = process.env.REACT_APP_API_URL ?? (process.env.NODE_ENV === 'production' ? '' : 'http://localhost:5000');
const api = axios.create({
    baseURL: `${API_BASE_URL}/api`, // API endpoints are under /api
});

api.interceptors.request.use(config => {
    const token = localStorage.getItem('token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

// --- FIX: Add a response interceptor for better error logging ---
// Enhanced response interceptor with automatic logout on 401
api.interceptors.response.use(
    response => response,
    error => {
        console.error("API Error:", error.response || error.message);
        const status = error.response?.status;
        const url = error.config?.url || '';

        // Auto-logout on 401 (expired token) — but NOT for a failed login attempt,
        // which legitimately returns 401 and should surface an error, not reload.
        if (status === 401 && !url.includes('/login')) {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            window.location.reload();
        }

        // Plan limit hit -> prompt to upgrade (skip billing calls themselves).
        if (status === 402 && !url.includes('/billing')) {
            window.dispatchEvent(new CustomEvent('sb:upgrade-required', {
                detail: { message: error.response?.data?.msg || error.response?.data?.message || 'Upgrade required to continue.' }
            }));
        }

        return Promise.reject(error);
    }
);


// --- Prevent a double-click (or any re-entrant call) from firing the same
// mutating request twice. Every apiService method is wrapped below so that if
// it's called again with identical arguments while the first call is still
// in flight, the caller gets back the SAME promise instead of a second HTTP
// request -- e.g. clicking "Mark Paid" twice quickly only ever hits the API
// once. This is a network-layer safety net that covers every button in the
// app without each component needing its own guard; UI-level disabling
// (better visual feedback) is layered on top for specific hot-path buttons.
function hasNonSerializableArg(args) {
    return args.some(a =>
        (typeof FormData !== 'undefined' && a instanceof FormData) ||
        (typeof File !== 'undefined' && a instanceof File) ||
        (typeof Blob !== 'undefined' && a instanceof Blob)
    );
}

function dedupeInFlight(fn) {
    const inFlight = new Map();
    return (...args) => {
        // Can't safely build a cache key for these (e.g. multipart uploads) --
        // just call through rather than risk an incorrect match.
        if (hasNonSerializableArg(args)) {
            return fn(...args);
        }
        const key = JSON.stringify(args);
        if (inFlight.has(key)) {
            return inFlight.get(key);
        }
        const promise = Promise.resolve(fn(...args)).finally(() => {
            inFlight.delete(key);
        });
        inFlight.set(key, promise);
        return promise;
    };
}

// Define API functions
const rawApiService = {
    api: api, // Export raw axios instance for generic requests
    login: (credentials) => api.post('/login', credentials),
    register: (credentials) => api.post('/register', credentials),
    verifyEmail: (token) => api.post('/verify-email', { token }),
    forgotPassword: (email) => api.post('/forgot-password', { email }),
    resetPassword: (token, new_password) => api.post('/reset-password', { token, new_password }),
    // Billing
    tenantMe: () => api.get('/tenant/me'),
    listPlans: () => api.get('/plans'),
    billingConfig: () => api.get('/billing/config'),
    billingCheckout: (plan) => api.post('/billing/checkout', { plan }),
    billingPortal: () => api.post('/billing/portal'),
    billingContact: (payload) => api.post('/billing/contact', payload),
    billingWhishCheckout: (cycle) => api.post('/billing/whish/checkout', { cycle }),
    // Platform super-admin
    adminTenants: () => api.get('/admin/tenants'),
    adminSuspendTenant: (id) => api.post(`/admin/tenants/${id}/suspend`),
    adminReactivateTenant: (id) => api.post(`/admin/tenants/${id}/reactivate`),
    adminDeleteTenant: (id) => api.delete(`/admin/tenants/${id}`),
    adminSetPlan: (id, plan, extra = {}) => api.post(`/admin/tenants/${id}/set-plan`, { plan, ...extra }),
    adminUpgradeRequests: () => api.get('/admin/upgrade-requests'),

    // User Management API methods
    fetchUsers: () => api.get('/users'),
    createUser: (data) => api.post('/users', data),
    updateUser: (userId, data) => api.put(`/users/${userId}`, data),
    deleteUser: (userId) => api.delete(`/users/${userId}`),

    // Reseller API methods
    fetchResellers: () => api.get('/resellers'),
    addReseller: (data) => api.post('/resellers', data),
    updateReseller: (id, data) => api.put(`/resellers/${id}`, data),
    deleteReseller: (id) => api.delete(`/resellers/${id}`),
    addResellerCredit: (id, data) => api.post(`/resellers/${id}/add_credit`, data),
    applyResellerDiscount: (id, data) => api.post(`/resellers/${id}/apply_discount`, data),
    collectResellerPayment: (id, data) => api.post(`/resellers/${id}/collect_payment`, data),
    getResellerHistory: (id) => api.get(`/resellers/${id}/history`),

    // Upstream Provider API methods (Concept A -- bridged RADIUS subresellers;
    // see docs/superpowers/specs/2026-08-12-network-enforcement-design.md)
    fetchUpstreamProviders: () => api.get('/upstream-providers'),
    addUpstreamProvider: (data) => api.post('/upstream-providers', data),
    updateUpstreamProvider: (id, data) => api.put(`/upstream-providers/${id}`, data),
    deleteUpstreamProvider: (id) => api.delete(`/upstream-providers/${id}`),
    getUpstreamProviderHistory: (id) => api.get(`/upstream-providers/${id}/history`),
    topupUpstreamProvider: (id, data) => api.post(`/upstream-providers/${id}/topup`, data),
    recordUpstreamRenewalCost: (id, data) => api.post(`/upstream-providers/${id}/renewal-cost`, data),

    // Mikrotik Server API methods (Concept B -- self-hosted local PPPoE)
    fetchMikrotikServers: () => api.get('/mikrotik-servers'),
    addMikrotikServer: (data) => api.post('/mikrotik-servers', data),
    updateMikrotikServer: (id, data) => api.put(`/mikrotik-servers/${id}`, data),
    deleteMikrotikServer: (id) => api.delete(`/mikrotik-servers/${id}`),
    testMikrotikConnection: (id) => api.post(`/mikrotik-servers/${id}/test-connection`),

    // Customer <-> Mikrotik live actions (staff-confirmed only, see spec)
    fetchCustomerMikrotikStatus: (customerId) => api.get(`/customers/${customerId}/mikrotik-status`),
    suspendCustomerMikrotik: (customerId) => api.post(`/customers/${customerId}/mikrotik-suspend`),
    unsuspendCustomerMikrotik: (customerId) => api.post(`/customers/${customerId}/mikrotik-unsuspend`),

    // Customer <-> Upstream Portal read-only status sync (staff-triggered, see spec)
    syncCustomerUpstreamStatus: (customerId) => api.post(`/customers/${customerId}/upstream-status-sync`),

    // Supplier API methods
    fetchSuppliers: () => api.get('/suppliers'),
    addSupplier: (data) => api.post('/suppliers', data),
    updateSupplier: (id, data) => api.put(`/suppliers/${id}`, data),
    deleteSupplier: (id) => api.delete(`/suppliers/${id}`),
    fetchSupplierPayments: (id) => api.get(`/suppliers/${id}/payments`),
    recordSupplierPayment: (id, data) => api.post(`/suppliers/${id}/payments`, data),
    fetchSupplierHistory: (id) => api.get(`/suppliers/${id}/history`),
    fixSupplierBalance: (id, data) => api.put(`/suppliers/${id}/fix-balance`, data),

    // Employee / Payroll API methods
    fetchEmployees: () => api.get('/employees'),
    addEmployee: (data) => api.post('/employees', data),
    updateEmployee: (id, data) => api.put(`/employees/${id}`, data),
    deleteEmployee: (id) => api.delete(`/employees/${id}`),
    fetchEmployeeCharges: (id) => api.get(`/employees/${id}/charges`),
    addEmployeeCharge: (id, data) => api.post(`/employees/${id}/charges`, data),
    fetchEmployeePayments: (id) => api.get(`/employees/${id}/payments`),
    recordEmployeePayment: (id, data) => api.post(`/employees/${id}/payments`, data),
    fetchEmployeeHistory: (id) => api.get(`/employees/${id}/history`),
    fixEmployeeBalance: (id, data) => api.put(`/employees/${id}/fix-balance`, data),


    fetchCustomers: async (page = 1, perPage = 999, searchQuery = '', sort_by = 'expiry_date', reseller_id = '') => {
        const response = await api.get(`/customers`, { params: { page: page, per_page: perPage, search: searchQuery, sort_by: sort_by, reseller_id: reseller_id } });
        return response.data; // Returns the data object directly
    },
    addCustomer: (customerData) => api.post(`/customers`, customerData),
    updateCustomer: (customerId, customerData) => api.put(`/customers/${customerId}`, customerData),
    // --- FIX: Ensure all API calls consistently return response.data ---
    fetchSubscriptionPlans: async () => {
        const response = await api.get(`/subscription_plans`);
        return response.data;
    },
    addSubscriptionPlan: (planData) => api.post(`/subscription_plans`, planData),
    updateSubscriptionPlan: (planId, planData) => api.put(`/subscription_plans/${planId}`, planData),
    deleteSubscriptionPlan: (planId) => api.delete(`/subscription_plans/${planId}`),
    fetchPayments: (customerId, status, startDate, endDate, searchQuery, collectedBy, collectedDate, sort_by = 'billed_date', sort_desc = 'true', paidDateStart, paidDateEnd) => api.get(`/payments`, { params: { customer_id: customerId, status: status, start_date: startDate, end_date: endDate, search_query: searchQuery, collected_by: collectedBy, collected_date: collectedDate, sort_by: sort_by, sort_desc: sort_desc, paid_date_start: paidDateStart, paid_date_end: paidDateEnd } }),
    deletePayment: (paymentId) => api.delete(`/payments/${paymentId}`),
    markPaymentAsPaid: (paymentId, data = {}) => api.put(`/payments/${paymentId}/mark_paid`, data),
    markPaymentGratis: (paymentId, note) => api.put(`/payments/${paymentId}/mark_gratis`, { note }),
    revertPayment: (paymentId, reason) => api.put(`/payments/${paymentId}/revert`, { reason }),
    cancelSubscription: (customerId) => api.put(`/customers/${customerId}/cancel_subscription`),
    activateSubscription: (customerId) => api.put(`/customers/${customerId}/activate_subscription`),
    deleteCustomer: (customerId) => api.delete(`/customers/${customerId}`),
    // Bulk actions: one HTTP request for a whole selection instead of one per row.
    bulkMarkPaymentsPaid: (paymentIds) => api.post('/payments/bulk_mark_paid', { payment_ids: paymentIds }),
    bulkDeletePayments: (paymentIds) => api.post('/payments/bulk_delete', { payment_ids: paymentIds }),
    bulkRenewSubscriptions: (customerIds) => api.post('/customers/bulk_renew_subscription', { customer_ids: customerIds }),
    bulkCancelSubscriptions: (customerIds) => api.post('/customers/bulk_cancel_subscription', { customer_ids: customerIds }),
    bulkDeleteCustomers: (customerIds) => api.post('/customers/bulk_delete', { customer_ids: customerIds }),
    fetchBusinessSettings: () => api.get('/business-settings'),
    saveBusinessSettings: (formData) => api.post('/business-settings', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
    }),
    fetchWhatsAppSettings: () => api.get('/whatsapp-settings'),
    saveWhatsAppSettings: (data) => api.post('/whatsapp-settings', data),
    subscribeWaba: () => api.post('/whatsapp/subscribe-waba'),
    tenantWhishSettings: () => api.get('/tenant-whish-settings'),
    saveTenantWhishSettings: (payload) => api.post('/tenant-whish-settings', payload),
    getPublicPayLink: () => api.get('/tenant/whish/public-pay-link'),
    regeneratePublicPayLink: () => api.post('/tenant/whish/public-pay-link/regenerate'),
    resendWhishPaymentLink: (customerId, paymentId) => api.post(`/customers/${customerId}/payments/${paymentId}/whish-link/resend`),
    emailWhishPaymentLink: (customerId, paymentId, email, payUrl) => api.post(`/customers/${customerId}/payments/${paymentId}/whish-link/email`, { email, pay_url: payUrl }),
    sendWhatsappReminder: (customerId, templateType = 'payment_reminder') => api.post(`/customers/${customerId}/send-whatsapp-reminder`, { template_type: templateType }),
    fetchReceipt: (paymentId) => api.get(`/receipt/${paymentId}`),
    addCustomerPayment: (paymentData) => api.post(`/payments`, paymentData),
    renewSubscription: (customerId) => api.post(`/customers/${customerId}/renew_subscription`),
    fetchCustomerBalance: (customerId) => api.get(`/customers/${customerId}/balance`),
    generateFuturePayments: (data) => api.post('/payments/generate_future', data),
    fetchReceiptLogs: (searchQuery, printed_filter = 'false', sort_by = 'billing_date', sort_desc = 'true') => api.get('/receipts/with-current-balance', { params: { search_query: searchQuery, printed: printed_filter, sort_by: sort_by, sort_desc: sort_desc } }),
    generateReceipts: (data) => api.post('/receipts/generate', data),
    logReceiptPrint: (data) => api.post('/receipts/log_print', data),
    deleteReceipt: (receiptId) => api.delete(`/receipts/${receiptId}`),

    // New Expense API methods
    fetchExpenses: (startDate, endDate) => api.get(`/expenses`, { params: { start_date: startDate, end_date: endDate } }),
    addExpense: (expenseData) => api.post(`/expenses`, expenseData),
    updateExpense: (expenseId, expenseData) => api.put(`/expenses/${expenseId}`, expenseData),
    deleteExpense: (expenseId) => api.delete(`/expenses/${expenseId}`),

    fetchExpenseCategories: () => api.get('/expense_categories'),
    addExpenseCategory: (categoryData) => api.post('/expense_categories', categoryData),
    updateExpenseCategory: (categoryId, categoryData) => api.put(`/expense_categories/${categoryId}`, categoryData),
    deleteExpenseCategory: (categoryId) => api.delete(`/expense_categories/${categoryId}`),

    fetchSectors: () => api.get('/sectors'),
    addSector: (sectorData) => api.post('/sectors', sectorData),
    updateSector: (sectorId, sectorData) => api.put(`/sectors/${sectorId}`, sectorData),
    deleteSector: (sectorId) => api.delete(`/sectors/${sectorId}`),

    fetchDashboardMetrics: (startDate, endDate) => api.get('/dashboard', { params: {
        ...(startDate ? { start_date: startDate } : {}),
        ...(endDate ? { end_date: endDate } : {})
    } }),

    // Reports
    fetchMonthlyRevenue: () => api.get('/reports/monthly-revenue'),
    fetchTotalSales: () => api.get('/reports/total-sales'),
    fetchUnpaidPayments: () => api.get('/reports/unpaid-payments'),
    fetchOverduePayments: () => api.get('/reports/overdue'),
    fetchCustomerNumbers: () => api.get('/reports/customer-numbers'),
    fetchExpensesTotal: () => api.get('/reports/expenses-total'),
    fetchActiveSubscriptionsByPlan: () => api.get('/reports/active-subscriptions-by-plan'),
    fetchFinancialReport: (startDate, endDate) => api.get(`/reports/financial`, { params: { start_date: startDate, end_date: endDate } }),
    fetchCollectorProgressReport: (startDate, endDate) => api.get('/reports/collector-progress', { params: { start_date: startDate, end_date: endDate } }),

    // Service Management
    fetchServiceStatuses: () => api.get('/service-statuses'), // Note: a new endpoint will be added to app.py
    fetchSupportTickets: () => api.get('/support-tickets'),
    fetchServiceOutages: () => api.get('/service-outages'),
    createSupportTicket: (data) => api.post('/support-tickets', data),
    updateSupportTicket: (ticketId, data) => api.put(`/support-tickets/${ticketId}`, data),
    deleteSupportTicket: (ticketId) => api.delete(`/support-tickets/${ticketId}`),
    bulkDeleteSupportTickets: (ticketIds) => api.post('/support-tickets/bulk_delete', { ticket_ids: ticketIds }),
    createServiceOutage: (data) => api.post('/service-outages', data),
    updateServiceOutage: (outageId, data) => api.put(`/service-outages/${outageId}`, data),
    updateServiceStatusById: (statusId, data) => api.put(`/service-statuses/${statusId}`, data),
    
    // Bulk Messaging
    sendBulkMessage: (payload) => api.post('/messages/bulk_send', payload),
    fetchMetaTemplates: () => api.get('/whatsapp/templates'),
    fetchWhatsAppTemplates: () => api.get('/whatsapp/templates'),
    syncWhatsAppTemplates: () => api.post('/whatsapp/templates/sync'),
    createWhatsAppTemplate: (data) => api.post('/whatsapp/templates', data),
    updateWhatsAppTemplate: (id, data) => api.put(`/whatsapp/templates/${id}`, data),
    deleteWhatsAppTemplate: (id) => api.delete(`/whatsapp/templates/${id}`),
    uploadWhatsAppTemplateSample: (formData) => api.post('/whatsapp/templates/upload-sample', formData,
        { headers: { 'Content-Type': 'multipart/form-data' } }),
};

export const apiService = Object.fromEntries(
    Object.entries(rawApiService).map(([name, value]) =>
        // `api` is the raw axios instance -- itself a callable function with
        // .get/.post/etc. attached. Wrapping it in dedupeInFlight would replace
        // it with a plain function missing those methods, so leave it as-is.
        [name, (name !== 'api' && typeof value === 'function') ? dedupeInFlight(value) : value]
    )
);

// --- Context for shared state ---
export const AppContext = createContext();

export const AppContextProvider = ({ children }) => {
    const [token, setToken] = useState(localStorage.getItem('token'));
    const [user, setUser] = useState(JSON.parse(localStorage.getItem('user')));
    const [isAuthenticated, setIsAuthenticated] = useState(!!token);
    const [snackbar, setSnackbar] = useState({ open: false, message: '', severity: 'info' });

    useEffect(() => {
        if (token) {
            localStorage.setItem('token', token);
            localStorage.setItem('user', JSON.stringify(user));
            setIsAuthenticated(true);
        } else {
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            setIsAuthenticated(false);
        }
    }, [token, user]);

    // Surface plan-limit (402) prompts raised by the axios interceptor.
    useEffect(() => {
        const onUpgrade = (e) => setSnackbar({
            open: true,
            message: `${e.detail?.message || 'Upgrade required.'} Open Billing & Plan to upgrade.`,
            severity: 'warning',
        });
        window.addEventListener('sb:upgrade-required', onUpgrade);
        return () => window.removeEventListener('sb:upgrade-required', onUpgrade);
    }, []);

    const login = async (credentials) => {
        const response = await apiService.login(credentials);
        setToken(response.data.access_token);
        setUser(response.data.user);
        return response;
    };

    const logout = () => {
        setToken(null);
        setUser(null);
    };

    const value = {
        apiService,
        snackbar,
        setSnackbar,
        token,
        user,
        isAuthenticated,
        login,
        logout
    };

    return (
        <AppContext.Provider value={value}>
            {children}
        </AppContext.Provider>
    );
};

export const useAppContext = () => {
    const context = useContext(AppContext);
    if (!context) {
        throw new Error('useAppContext must be used within an AppContextProvider');
    }
    return context;
};

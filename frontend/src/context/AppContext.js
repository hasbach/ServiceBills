// src/context/AppContext.js
import React, { createContext, useContext, useState, useEffect } from 'react';
import axios from 'axios';

// --- API Service Abstraction ---
// Use Flask backend URL during development, empty for production builds
const API_BASE_URL = process.env.NODE_ENV === 'production' ? '' : 'http://localhost:5000';
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

        // Automatically logout on 401 (token expired)
        if (error.response?.status === 401) {
            console.log("Token expired, logging out...");
            localStorage.removeItem('token');
            localStorage.removeItem('user');
            window.location.reload(); // Force reload to show login screen
        }

        return Promise.reject(error);
    }
);


// Define API functions
export const apiService = {
    api: api, // Export raw axios instance for generic requests
    login: (credentials) => api.post('/login', credentials),
    register: (credentials) => api.post('/register', credentials),
    
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

    // Supplier API methods
    fetchSuppliers: () => api.get('/suppliers'),
    addSupplier: (data) => api.post('/suppliers', data),
    updateSupplier: (id, data) => api.put(`/suppliers/${id}`, data),
    deleteSupplier: (id) => api.delete(`/suppliers/${id}`),
    fetchSupplierPayments: (id) => api.get(`/suppliers/${id}/payments`),
    recordSupplierPayment: (id, data) => api.post(`/suppliers/${id}/payments`, data),
    fetchSupplierHistory: (id) => api.get(`/suppliers/${id}/history`),
    fixSupplierBalance: (id, data) => api.put(`/suppliers/${id}/fix-balance`, data),


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
    fetchPayments: (customerId, status, startDate, endDate, searchQuery, collectedBy, collectedDate, sort_by = 'billed_date', sort_desc = 'true') => api.get(`/payments`, { params: { customer_id: customerId, status: status, start_date: startDate, end_date: endDate, search_query: searchQuery, collected_by: collectedBy, collected_date: collectedDate, sort_by: sort_by, sort_desc: sort_desc } }),
    deletePayment: (paymentId) => api.delete(`/payments/${paymentId}`),
    markPaymentAsPaid: (paymentId, data = {}) => api.put(`/payments/${paymentId}/mark_paid`, data),
    cancelSubscription: (customerId) => api.put(`/customers/${customerId}/cancel_subscription`),
    activateSubscription: (customerId) => api.put(`/customers/${customerId}/activate_subscription`),
    deleteCustomer: (customerId) => api.delete(`/customers/${customerId}`),
    fetchBusinessSettings: () => api.get('/business-settings'),
    saveBusinessSettings: (formData) => api.post('/business-settings', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
    }),
    fetchWhatsAppSettings: () => api.get('/whatsapp-settings'),
    saveWhatsAppSettings: (data) => api.post('/whatsapp-settings', data),
    subscribeWaba: () => api.post('/whatsapp/subscribe-waba'),
    fetchSystemUpdateStatus: () => api.get('/system-update/status'),
    saveSystemUpdateSettings: (data) => api.post('/system-update/settings', data),
    checkForSystemUpdates: () => api.post('/system-update/check'),
    applySystemUpdate: () => api.post('/system-update/apply'),
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

    fetchDashboardMetrics: () => api.get('/dashboard'),

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
    createServiceOutage: (data) => api.post('/service-outages', data),
    updateServiceOutage: (outageId, data) => api.put(`/service-outages/${outageId}`, data),
    updateServiceStatusById: (statusId, data) => api.put(`/service-statuses/${statusId}`, data),
    
    // Bulk Messaging
    sendBulkMessage: (payload) => api.post('/messages/bulk_send', payload),
    fetchMetaTemplates: () => api.get('/whatsapp/templates'),
};

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

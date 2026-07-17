import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Bar, Pie } from 'react-chartjs-2';
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    BarElement,
    Title,
    Tooltip,
    Legend,
    ArcElement,
} from 'chart.js';
import { apiService, useAppContext } from '../context/AppContext.js'; // CHANGED: Import context

// Register the required components
ChartJS.register(
    CategoryScale,
    LinearScale,
    BarElement,
    Title,
    Tooltip,
    Legend,
    ArcElement
);

// Create axios instance with JWT token
const API_BASE_URL = process.env.REACT_APP_API_URL ?? (process.env.NODE_ENV === 'production' ? '' : 'http://localhost:5000');
const api = axios.create({
    baseURL: `${API_BASE_URL}/api`,
});
api.interceptors.request.use((config) => {
    const token = localStorage.getItem('token');
    if (token) {
        config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
});

const ReportsView = () => {
    const [monthlyRevenue, setMonthlyRevenue] = useState([]);
    const [totalSales, setTotalSales] = useState([]);
    const [unpaidPayments, setUnpaidPayments] = useState([]);
    const [customerNumbers, setCustomerNumbers] = useState([]);
    const [expensesTotal, setExpensesTotal] = useState([]);
    const [activeSubscriptionsByPlan, setActiveSubscriptionsByPlan] = useState([]);

    useEffect(() => {
        apiService.fetchMonthlyRevenue().then(res => setMonthlyRevenue(res.data)).catch(console.error);
       // api.get('/reports/total-sales').then(res => setTotalSales(res.data)).catch(console.error);
        apiService.fetchTotalSales().then(res => setTotalSales(res.data)).catch(console.error);
        apiService.fetchUnpaidPayments().then(res => setUnpaidPayments(res.data)).catch(console.error);
        apiService.fetchCustomerNumbers().then(res => setCustomerNumbers(res.data)).catch(console.error);
        apiService.fetchExpensesTotal().then(res => setExpensesTotal(res.data)).catch(console.error);
        apiService.fetchActiveSubscriptionsByPlan().then(res => setActiveSubscriptionsByPlan(res.data)).catch(console.error);
    }, []);

    const createChartData = (data, label, color) => ({
        labels: data.map(item => item.month),
        datasets: [
            {
                label: label,
                data: data.map(item => item.value),
                backgroundColor: color,
                borderColor: color,
                borderWidth: 1
            }
        ]
    });

    // Create pie chart data for subscription plans
    const createSubscriptionPlanChartData = (data) => {
        const colors = [
            'rgba(255, 99, 132, 0.6)',
            'rgba(54, 162, 235, 0.6)',
            'rgba(255, 205, 86, 0.6)',
            'rgba(75, 192, 192, 0.6)',
            'rgba(153, 102, 255, 0.6)',
            'rgba(255, 159, 64, 0.6)',
            'rgba(199, 199, 199, 0.6)',
            'rgba(83, 102, 255, 0.6)',
        ];

        return {
            labels: data.map(item => item.plan_name),
            datasets: [
                {
                    label: 'Active Subscriptions',
                    data: data.map(item => item.active_count),
                    backgroundColor: colors.slice(0, data.length),
                    borderColor: colors.slice(0, data.length).map(color => color.replace('0.6', '1')),
                    borderWidth: 2
                }
            ]
        };
    };

    const pieChartOptions = {
        responsive: true,
        plugins: {
            legend: {
                position: 'right',
                labels: {
                    padding: 20,
                    usePointStyle: true
                }
            },
            title: {
                display: true,
                text: 'Distribution of Active Subscriptions'
            },
            tooltip: {
                callbacks: {
                    label: function (context) {
                        const total = context.dataset.data.reduce((a, b) => a + b, 0);
                        const percentage = ((context.parsed / total) * 100).toFixed(1);
                        return `${context.label}: ${context.parsed} (${percentage}%)`;
                    }
                }
            }
        }
    };

    return (
        <div style={{ padding: '20px' }}>
            <h2 style={{ textAlign: 'center', marginBottom: '20px' }}>Monthly Reports</h2>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', marginBottom: '20px' }}>
                <ChartBox title="Net Revenue Received" data={createChartData(monthlyRevenue, 'Net Revenue', 'rgba(75,192,192,0.4)')} />
                <ChartBox title="Total Sales (Paid)" data={createChartData(totalSales, 'Total Sales', 'rgba(255,99,132,0.4)')} />
                <ChartBox title="Total Unpaid Payments" data={createChartData(unpaidPayments, 'Unpaid Payments', 'rgba(255,159,64,0.4)')} />
                <ChartBox title="Customer Numbers" data={createChartData(customerNumbers, 'Customers', 'rgba(54,162,235,0.4)')} />
                <ChartBox title="Expenses Total" data={createChartData(expensesTotal, 'Expenses', 'rgba(153,102,255,0.4)')} />

                {/* New subscription plans chart */}
                <div style={chartContainerStyle}>
                    <h3>Active Subscriptions by Plan</h3>
                    {activeSubscriptionsByPlan.length > 0 ? (
                        <Pie
                            data={createSubscriptionPlanChartData(activeSubscriptionsByPlan)}
                            options={pieChartOptions}
                        />
                    ) : (
                        <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
                            No active subscriptions found
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

const ChartBox = ({ title, data }) => (
    <div style={chartContainerStyle}>
        <h3>{title}</h3>
        <Bar data={data} options={chartOptions} />
    </div>
);

const chartContainerStyle = {
    backgroundColor: '#ffffff',
    padding: '20px',
    borderRadius: '8px',
    boxShadow: '0 2px 4px rgba(0, 0, 0, 0.1)',
};

const chartOptions = {
    responsive: true,
    plugins: {
        legend: { position: 'top' },
        title: { display: true },
    },
};

export default ReportsView;
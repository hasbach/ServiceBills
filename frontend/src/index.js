import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App';
import * as serviceWorkerRegistration from './serviceWorkerRegistration';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);

// LOCAL-TEST ONLY (uncommitted): the service worker turns any failed fetch
// into a fake 200 "You are offline.", which the dashboard then chokes on and,
// with no error boundary, unmounts the whole app -- making login look broken.
// Tracked separately as task_78b19c45. Restore this line before committing.
// serviceWorkerRegistration.register();

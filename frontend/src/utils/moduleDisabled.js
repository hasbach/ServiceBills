// A module-disabled 403 only warrants the "not included in your plan" snackbar
// for user-initiated writes; a GET is a passive page-load fetch.
export const shouldNotifyModuleDisabled = (error) =>
    error?.response?.status === 403 &&
    !!error.response?.data?.module &&
    String(error.config?.method || '').toLowerCase() !== 'get';

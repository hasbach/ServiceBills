import { createTheme } from '@mui/material/styles';
import { brand } from './brand/tokens';

// The single servicesBills MUI theme, built from brand tokens.
const theme = createTheme({
  palette: {
    mode: 'light',
    primary: brand.colors.primary,
    secondary: brand.colors.secondary,
    success: { main: brand.colors.success },
    error: { main: brand.colors.error },
    warning: { main: brand.colors.warning },
    info: { main: brand.colors.info },
    background: { default: brand.colors.bg, paper: brand.colors.paper },
    text: { primary: brand.colors.textPrimary, secondary: brand.colors.textSecondary },
  },
  typography: {
    fontFamily: brand.fontFamily,
    h4: { fontWeight: 700 },
    h5: { fontWeight: 700 },
    h6: { fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  shape: { borderRadius: brand.radius },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { borderRadius: brand.radius } },
    },
    MuiPaper: { styleOverrides: { root: { backgroundImage: 'none' } } },
    MuiAppBar: { defaultProps: { elevation: 0 } },
  },
});

export default theme;

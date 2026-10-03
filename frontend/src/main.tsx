import {createRoot} from 'react-dom/client';
import {FluentProvider, webLightTheme} from '@fluentui/react-components';
import {App} from './App';
import {ConfirmProvider} from './ConfirmDialog';
import './styles.css';

const theme = {...webLightTheme,
  colorBrandBackground: '#186653', colorBrandBackgroundHover: '#125743', colorBrandBackgroundPressed: '#0c4837',
  colorBrandForeground1: '#186653', colorBrandStroke1: '#186653', colorBrandBackground2: '#e9f2ec',
  colorCompoundBrandStroke: '#186653', colorCompoundBrandForeground1: '#186653', colorCompoundBrandBackground: '#186653',
  colorCompoundBrandBackgroundHover: '#125743', colorCompoundBrandBackgroundPressed: '#0c4837',
  borderRadiusMedium: '8px', borderRadiusLarge: '12px', fontFamilyBase: '"Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif'};
createRoot(document.getElementById('root')!).render(<FluentProvider theme={theme}><ConfirmProvider><App/></ConfirmProvider></FluentProvider>);

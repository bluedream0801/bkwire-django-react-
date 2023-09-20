import { Theme } from '@mui/material';
import { ZIndexOptions } from '@mui/material/styles/zIndex';

const getZIndex = (theme: Theme): ZIndexOptions => ({
  mobileStepper: 1000,
  speedDial: 1050,
  appBar: 1100,
  drawer: 1200,
  modal: 1300,
  snackbar: 1400,
  tooltip: 1500,
});

export default getZIndex;

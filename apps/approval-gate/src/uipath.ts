import { CodedActionAppService } from '@uipath/coded-action-app';

// No platform SDK services needed — the form only displays inputs passed by the
// case and returns an approve/reject decision.
export const codedActionAppService = new CodedActionAppService();

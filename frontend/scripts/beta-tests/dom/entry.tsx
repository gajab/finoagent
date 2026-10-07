import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
// @ts-ignore
import { act as testAct } from 'react-dom/test-utils';
import BetaTradeInspector from '../../../src/beta/BetaTradeInspector';
import { deriveTradeState } from '../../../src/beta/betaState';
export const act = (React as any).act || testAct;
export { React, createRoot, MemoryRouter, BetaTradeInspector, deriveTradeState };

import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
afterEach(cleanup);
Object.defineProperty(window,'matchMedia',{value:vi.fn((q:string)=>({matches:false,media:q,addEventListener:vi.fn(),removeEventListener:vi.fn(),addListener:vi.fn(),removeListener:vi.fn()}))});
Element.prototype.scrollTo=vi.fn();

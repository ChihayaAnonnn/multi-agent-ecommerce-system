import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { QueryClientProvider,QueryClient } from '@tanstack/react-query';
import App from './App';
test('renders storefront with a single shopping input',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({items:[],next_cursor:null}),{status:200})));
 render(<MemoryRouter initialEntries={['/demo/commerce']}><QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><App /></QueryClientProvider></MemoryRouter>);
 expect(screen.getByRole('heading',{name:'为日常挑选'})).toBeVisible();
 expect(screen.getAllByRole('textbox')).toHaveLength(1);
 expect(screen.queryByRole('searchbox')).not.toBeInTheDocument();
});

import { apiRequest,post } from '../../api/client';
import type { ProductPage,ProductResolved } from '../../api/contracts';
export function getProducts(cursor:string|null,signal?:AbortSignal):Promise<ProductPage>{return apiRequest(`/api/v1/storefront/products?limit=24${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`,{signal});}
export function resolveProducts(ids:string[],signal?:AbortSignal):Promise<ProductResolved>{return apiRequest('/api/v1/storefront/products/resolve',{...post({product_ids:ids}),signal});}

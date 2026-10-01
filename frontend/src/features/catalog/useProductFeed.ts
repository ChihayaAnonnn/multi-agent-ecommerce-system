import { useRef } from 'react';
import { useInfiniteQuery,useQuery,keepPreviousData } from '@tanstack/react-query';
import { getProducts,resolveProducts } from './api';
import type { Product } from '../../api/contracts';
export function useProductFeed(candidateProductIds:string[]|null){
 const last=useRef<Product[]>([]);const key=useRef('catalog');
 const catalog=useInfiniteQuery({queryKey:['storefront','catalog'],queryFn:({pageParam,signal})=>getProducts(pageParam,signal),initialPageParam:null as string|null,getNextPageParam:p=>p.next_cursor??undefined,enabled:candidateProductIds===null});
 const candidates=useQuery({queryKey:['storefront','candidates',candidateProductIds],queryFn:({signal})=>resolveProducts(candidateProductIds||[],signal),enabled:candidateProductIds!==null,placeholderData:keepPreviousData});
 const source=candidateProductIds===null?catalog:candidates;
 const ready=candidateProductIds===null?catalog.data?.pages.flatMap(p=>p.items):candidates.data?.items;
 if(ready){last.current=ready;if(candidateProductIds===null)key.current='catalog';else if(!candidates.isPlaceholderData)key.current=candidateProductIds.join(',');}
 return {items:ready||last.current,isLoading:source.isPending,isFetching:source.isFetching,error:source.error,missingIds:candidateProductIds!==null?candidates.data?.missing_product_ids||[]:[],hasMore:candidateProductIds===null&&catalog.hasNextPage,loadMore:()=>catalog.fetchNextPage(),retry:()=>source.refetch(),isLoadingMore:catalog.isFetchingNextPage,viewKey:key.current};
}

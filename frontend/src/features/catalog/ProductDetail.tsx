import { useState,useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowUpRight,ImageOff } from 'lucide-react';
import { apiRequest } from '../../api/client';
import type { ProductDetail as Detail,Product,Price,Availability } from '../../api/contracts';
import type { Reference } from '../chat/sessionStore';
import { money,stockLabel } from '../../api/format';
import styles from './ProductDetail.module.css';
export function ProductDetail({id,onReference}:{id:string;onReference:(r:Reference)=>void}){
 const detail=useQuery({queryKey:['product',id],queryFn:({signal})=>apiRequest<Detail>(`/api/v1/catalog/products/${encodeURIComponent(id)}`,{signal})});
 const card=useQuery({queryKey:['card',id],queryFn:({signal})=>apiRequest<{items:Product[]}>('/api/v1/storefront/products/resolve',{method:'POST',body:JSON.stringify({product_ids:[id]}),signal})});
 const [selected,setSelected]=useState(''),[failed,setFailed]=useState(false);
 useEffect(()=>{setSelected('');setFailed(false);},[id]);
 const p=detail.data;const active=p?.skus.filter(s=>s.status==='active')||[];
 const sid=selected||card.data?.items[0]?.sku_id||active[0]?.id;
 const quote=useQuery({queryKey:['quote',sid],queryFn:({signal})=>apiRequest<{price:Price|null;availability:Availability;reason:string}>(`/api/v1/commerce/skus/${encodeURIComponent(sid!)}/quote`,{signal}),enabled:!!sid});
 if(detail.isPending)return <p className="muted">正在读取商品…</p>;
 if(detail.error||!p)return <div className="error-state"><p>暂时无法读取商品。</p><button onClick={()=>void detail.refetch()}>重试</button></div>;
 const image=p.media.find(m=>m.media_type==='image'&&m.url.startsWith('https://'))?.url;
 return <div className={styles.detail}>
  <div className={styles.image}>{image&&!failed?<img src={image} alt={p.name} referrerPolicy="no-referrer" onError={()=>setFailed(true)}/>:<div><ImageOff size={28}/><p>暂无图片</p></div>}</div>
  <span className={styles.category}>{p.brand||p.category.name}</span><h2>{p.name}</h2><div className={styles.price}>{money(quote.data?.price?.amount_minor)}<small>{quote.error?'报价暂不可用':quote.data?.reason==='purchasable'?'有货':stockLabel(quote.data?.reason||'stock_unknown')}</small></div>
  {active.length>1&&<div className={styles.skus} aria-label="商品规格">{active.map(s=><button key={s.id} aria-pressed={s.id===sid} onClick={()=>setSelected(s.id)}>{s.selected_values.map(v=>v.display_value).join(' / ')||s.sku_code}</button>)}</div>}
  <button className="primary-button" disabled={!sid} onClick={()=>onReference({product_id:id,sku_id:sid,name:p.name})}>向助手询问这件商品<ArrowUpRight size={17}/></button>
  <p className={styles.hint}>价格与库存将在购物确认时重新核验。</p>
  {p.description&&<section><h3>关于这件商品</h3><p className={styles.description}>{p.description}</p></section>}
  {p.attributes?.average_rating!=null&&<p className={styles.hint}>历史评分 {String(p.attributes.average_rating)}{p.attributes.rating_number!=null&&` · ${p.attributes.rating_number} 条评价`}</p>}
 </div>;
}

import type { Product } from '../../api/contracts';
import { ProductCard } from './ProductCard';
import styles from './ProductGrid.module.css';
export function ProductGrid({items,loading,onOpen}:{items:Product[];loading:boolean;onOpen:(p:Product)=>void}){
 return <div className={styles.grid} aria-label="商品展示" aria-busy={loading}>{!items.length&&loading?Array.from({length:6},(_,i)=><div key={i} className={styles.skeleton}><div/><span/><span/></div>):items.map(p=><ProductCard key={p.id} product={p} onOpen={onOpen}/>)}</div>;
}

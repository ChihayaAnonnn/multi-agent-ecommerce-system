import { motion,useReducedMotion } from 'motion/react';
import type { Product } from '../../api/contracts';
import { ProductCard } from './ProductCard';
import styles from './ProductGrid.module.css';
export function ProductGrid({items,loading,onOpen}:{items:Product[];loading:boolean;onOpen:(p:Product)=>void}){
 const reduced=useReducedMotion();
 return <div className={styles.grid} aria-label="商品展示" aria-busy={loading}>{!items.length&&loading?Array.from({length:6},(_,i)=><div key={i} className={styles.skeleton}><div/><span/><span/></div>):items.map((p,i)=><motion.div key={p.id} initial={{opacity:0,y:reduced?0:7}} whileInView={{opacity:1,y:0}} viewport={{once:true,amount:.1}} transition={{duration:reduced?.08:.18,delay:reduced?0:Math.min(i%24,3)*.025}}><ProductCard product={p} onOpen={onOpen}/></motion.div>)}</div>;
}

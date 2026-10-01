import { expect,test } from 'vitest';
import { panelMotion } from './motion';
test('reduced motion removes displacement and uses at most 80ms fade',()=>{
 expect(panelMotion(true).initial).toEqual({opacity:0,x:0});
 expect(panelMotion(true).exit).toEqual({opacity:0,x:0});
 expect(panelMotion(true).transition.duration).toBeLessThanOrEqual(.08);
 expect(panelMotion(false).transition.duration).toBeGreaterThan(.2);
});

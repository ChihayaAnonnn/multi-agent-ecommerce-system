import { test,expect,vi } from 'vitest';
import { useState } from 'react';
import { render,screen,fireEvent,waitFor } from '@testing-library/react';
import { Drawer } from './Drawer';
test('escape closes panel and restores trigger focus',async()=>{
 function Test(){const [open,setOpen]=useState(false);return <><button onClick={()=>setOpen(true)}>打开详情</button><Drawer open={open} onClose={()=>setOpen(false)} title="详情"><button>操作</button></Drawer></>;}
 render(<Test/>);const trigger=screen.getByRole('button',{name:'打开详情'});trigger.focus();fireEvent.click(trigger);
 await waitFor(()=>expect(screen.getByRole('dialog')).toBeVisible());fireEvent.keyDown(document,{key:'Escape'});
 await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());expect(trigger).toHaveFocus();
});

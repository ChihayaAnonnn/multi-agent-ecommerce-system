import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
const tls='/private/tmp/commerce-demo-tls';
const https=fs.existsSync(`${tls}/cert.pem`) ? {cert:fs.readFileSync(`${tls}/cert.pem`),key:fs.readFileSync(`${tls}/key.pem`)} : undefined;
// 后端保留来源校验；开发请求由可信同源代理转发。
const target=process.env.COMMERCE_API_TARGET || 'https://localhost:18443';
const proxy={target,changeOrigin:true,secure:false,configure:(p:any)=>p.on('proxyReq',(req:any)=>{req.setHeader('Origin',target);})};
export default defineConfig({plugins:[react()],base:'/demo/commerce/',server:{https,port:5173,proxy:{'/api':proxy,'/demo/commerce/guest':proxy}},build:{outDir:'dist'}});

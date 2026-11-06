/** Exercise the shipped browser module without a frontend build or external test packages. */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../src/bs_simulators/viewers/scenes.js',import.meta.url));
const {signalRange,scaleFraction,limitLabel,scenes}=await import('data:text/javascript;base64,'+source.toString('base64'));

test('complete bounds stay fixed even when observations exceed them',()=>{
 assert.deepEqual(signalRange({lower:-2,upper:3},[-100,100]),[-2,3]);
 assert.equal(scaleFraction(-100,[-2,3]),0);
 assert.equal(scaleFraction(100,[-2,3]),1);
 assert.equal(scaleFraction(0,[-2,3]),.4);
});
test('positive and negative measurement ranges have a meaningful zero reference',()=>{
 assert.equal(scaleFraction(0,[5,10]),0);
 assert.equal(scaleFraction(7.5,[5,10]),.5);
 assert.equal(scaleFraction(0,[-10,-5]),1);
 assert.equal(scaleFraction(-7.5,[-10,-5]),.5);
});
test('each partial bound is honored while the missing endpoint uses the fallback',()=>{
 assert.deepEqual(signalRange({lower:0},[-2,2]),[0,2]);
 assert.deepEqual(signalRange({upper:8},[0,14]),[0,8]);
 assert.deepEqual(signalRange({lower:3},[-1,1]),[3,5]);
 assert.deepEqual(signalRange({upper:-3},[-1,1]),[-5,-3]);
 assert.deepEqual(signalRange({lower:25},[0,25]),[25,50]);
});
test('unbounded and unnamed signals keep their fallback scale',()=>{
 assert.deepEqual(signalRange(undefined,[-.1,.1]),[-.1,.1]);
 assert.deepEqual(signalRange({lower:null,upper:null},[0,25]),[0,25]);
});
test('constant ranges never divide by zero',()=>{
 assert.deepEqual(signalRange({lower:2,upper:2},[-1,1]),[2,2]);
 assert.equal(scaleFraction(2,[2,2]),.5);
 assert.equal(scaleFraction(1,[2,2]),0);
 assert.equal(scaleFraction(3,[2,2]),1);
});
test('normalization avoids overflow for finite opposite endpoints',()=>{
 assert.equal(scaleFraction(0,[-1e308,1e308]),.5);
 assert.equal(scaleFraction(1e308,[-1e308,1e308]),1);
});
test('small limit labels are not rounded to zero',()=>{
 assert.equal(limitLabel(0),'0');
 assert.equal(limitLabel(.001),'1e-3');
 assert.equal(limitLabel(-.001),'-1e-3');
 assert.equal(limitLabel(6.5),'6.5');
});

function canvas(){
 const texts=[];
 const context=new Proxy({fillText(value){texts.push(value);}},{
  get(target,key){return key in target?target[key]:()=>{};},
 });
 return {texts,getBoundingClientRect:()=>({width:640,height:640}),getContext:()=>context};
}
globalThis.window={devicePixelRatio:1};
test('motor binds limits by state-vector position, preserves asymmetric scales and actual readings',()=>{
 const state={x:[.2,10,-.1]},original=JSON.stringify(state),surface=canvas();
 scenes['dc-motor'].reset();scenes['dc-motor'].observe(state);
 const ranges=scenes['dc-motor'].draw(surface,state,{outputs:[{},
  {name:'custom_speed',lower:-2,upper:3},{name:'custom_current',lower:0,upper:2}]});
 assert.deepEqual(ranges.map(value=>value.range),[[-2,3],[0,2]]);
 assert.equal(JSON.stringify(state),original);
 assert.ok(surface.texts.some(value=>value.includes('omega=10.00')&&value.includes('I=-0.100')));
});
test('neutralization honors gauge bounds while retaining the real out-of-range pH',()=>{
 const state={x:[-.000432,.000528,14,9],u:[15.6],parameters:{q1:16.6,d:.55},t:0};
 const original=JSON.stringify(state),surface=canvas();
 const ranges=scenes.neutralization.draw(surface,state,{outputs:[{},{},
  {name:'custom_level',lower:5,upper:50},{name:'custom_ph',lower:6,upper:8}]});
 assert.deepEqual(ranges.map(value=>value.range),[[6,8],[5,50]]);
 assert.equal(JSON.stringify(state),original);
 assert.ok(surface.texts.includes('9.00'));
});

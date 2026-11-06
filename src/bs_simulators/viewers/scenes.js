/** Canvas counterparts of benchmark-systems' Matplotlib scenes (schematics only). */
export const fmt=(v,n=2)=>Number.isFinite(v)?v.toFixed(n):'—';
const clamp=(v,lo,hi)=>Math.max(lo,Math.min(hi,v));

/** Preserve declared endpoints and infer only missing ones from a scene's fallback. */
export function signalRange(signal,fallback){
 const hasLower=Number.isFinite(signal?.lower),hasUpper=Number.isFinite(signal?.upper);
 let lower=hasLower?signal.lower:fallback[0],upper=hasUpper?signal.upper:fallback[1];
 const span=Math.max(fallback[1]-fallback[0],Math.abs(lower)*.1,Math.abs(upper)*.1,1e-3);
 if(hasLower&&!hasUpper&&upper<=lower)upper=lower+span;
 if(hasUpper&&!hasLower&&lower>=upper)lower=upper-span;
 if(lower>upper)throw new Error('Signal lower bound exceeds its upper bound');
 return [lower,upper];
}

/** Normalize a reading for drawing only; a zero-width range has its value at the centre. */
export function scaleFraction(value,[lower,upper]){
 if(lower===upper)return value<lower?0:value>upper?1:.5;
 const scale=Math.max(Math.abs(lower),Math.abs(upper),1);
 return clamp((clamp(value,lower,upper)/scale-lower/scale)/(upper/scale-lower/scale),0,1);
}

/** Keep small full-scale labels readable without rounding them to zero. */
export function limitLabel(value){
 if(value===0)return '0';
 return Math.abs(value)<.01||Math.abs(value)>=1e4
  ?value.toExponential(1).replace('.0e','e'):Number(value.toPrecision(4)).toString();
}

/** Map an equal-aspect model coordinate system into the responsive canvas. */
function plot(canvas,xmin,xmax,ymin,ymax,padding=12){
 const {width:w,height:h}=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
 canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);
 const c=canvas.getContext('2d');c.scale(dpr,dpr);
 const scale=Math.max(1e-9,Math.min((w-2*padding)/(xmax-xmin),(h-2*padding)/(ymax-ymin)));
 const left=(w-scale*(xmax-xmin))/2,top=(h-scale*(ymax-ymin))/2;
 const X=x=>left+(x-xmin)*scale,Y=y=>top+(ymax-y)*scale;
 const line=(x1,y1,x2,y2,color='#444',lw=1)=>{
  c.strokeStyle=color;c.lineWidth=lw;c.beginPath();c.moveTo(X(x1),Y(y1));c.lineTo(X(x2),Y(y2));c.stroke();
 };
 const text=(s,x,y,size=12,color='#222',align='center',bold=false)=>{
  c.font=(bold?'bold ':'')+size+'px Arial';c.fillStyle=color;c.textAlign=align;c.textBaseline='middle';c.fillText(s,X(x),Y(y));
 };
 const circle=(x,y,r,fill,stroke=null,lw=1)=>{
  c.beginPath();c.arc(X(x),Y(y),r*scale,0,2*Math.PI);
  if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=lw;c.stroke();}
 };
 const rect=(x,y,w0,h0,fill,stroke=null,r=0,lw=1)=>{
  c.beginPath();c.roundRect(X(x),Y(y+h0),w0*scale,h0*scale,r*scale);
  if(fill){c.fillStyle=fill;c.fill();}if(stroke){c.strokeStyle=stroke;c.lineWidth=lw;c.stroke();}
 };
 const arrow=(x1,y1,x2,y2,color='#444')=>{
  line(x1,y1,x2,y2,color,1.5);const a=Math.atan2(y2-y1,x2-x1),r=.12;
  line(x2,y2,x2-r*Math.cos(a-.45),y2-r*Math.sin(a-.45),color,1.5);
  line(x2,y2,x2-r*Math.cos(a+.45),y2-r*Math.sin(a+.45),color,1.5);
 };
 return {c,scale,X,Y,line,text,circle,rect,arrow};
}

function pendulum(canvas,state){
 const L=state?.parameters?.L||1,lim=1.5*L;
 const p=plot(canvas,-lim*1.30,lim*1.12,-lim*1.30,lim*1.12),{line,text,circle,c,X,Y}=p;
 line(-lim,-lim,lim,-lim,'#222');line(-lim,-lim,-lim,lim,'#222');
 line(-lim,lim,lim,lim,'#222');line(lim,-lim,lim,lim,'#222');
 for(let i=-3;i<=3;i++){
  const v=i*L/2;
  line(v,-lim,v,-lim-.04*L,'#222');text(fmt(v,L<.1?3:1),v,-lim-.16*L,12);
  line(-lim,v,-lim-.04*L,v,'#222');text(fmt(v,L<.1?3:1),-lim-.09*L,v,12,'#222','right');
 }
 text('x (m)',0,-lim-.36*L,12);
 c.save();c.translate(X(-lim-.36*L),Y(0));c.rotate(-Math.PI/2);c.fillText('y (m)',0,0);c.restore();
 line(-lim,0,lim,0,'#b8b8b8',.8);
 if(state){
  const [theta]=state.x,bx=L*Math.sin(theta),by=-L*Math.cos(theta);
  line(0,0,bx,by,'#696969',2);circle(bx,by,.08*L,'steelblue');circle(0,0,.04*L,'black');
 }
}

let omegaMax=1e-3,currentMax=1e-3;
function motor(canvas,state,signals={outputs:[]}){
 const p=plot(canvas,-2.5,2.5,-1.5,1.5),{line,text,circle,rect}=p;
 circle(0,0,.9,'rgba(65,105,225,.3)','royalblue',2);
 const [theta,omega,current]=state?.x||[0,0,0];
 line(0,0,.85*Math.sin(theta),.85*Math.cos(theta),'navy',3);
 const omegaRange=signalRange(signals.outputs[1],[-omegaMax,omegaMax]);
 const currentRange=signalRange(signals.outputs[2],[-currentMax,currentMax]);
 for(const [x,value,range,color,label] of [[1.2,omega,omegaRange,'steelblue','omega'],[1.7,current,currentRange,'tomato','I']]){
  rect(x,-.8,.3,1.6,'lightgray','gray',.02);
  const zero=-.8+1.6*scaleFraction(0,range),level=-.8+1.6*scaleFraction(value,range);
  rect(x+.02,Math.min(zero,level),.26,Math.abs(level-zero),color,null,.01);
  if(range[0]<=0&&range[1]>=0)line(x,zero,x+.3,zero,'#777',.6);
  text(limitLabel(range[1]),x+.15,.94,10,'#555');text(limitLabel(range[0]),x+.15,-.91,10,'#555');
  text(label,x+.15,-1.05,12,color==='tomato'?'firebrick':'navy');
 }
 text('theta='+fmt(theta*180/Math.PI,1)+'°   omega='+fmt(omega)+' rad/s   I='+fmt(current,3)+' A',0,-1.25,12,'dimgray');
 return [{label:'omega (rad/s)',range:omegaRange},{label:'I (A)',range:currentRange}];
}

/** A gauge matching the 210° to -30° sweep of the Matplotlib DialGauge. */
function gauge(p,cx,cy,value,range,label){
 const {c,X,Y,scale,circle,line,text}=p,r=.65,f=scaleFraction(value,range),a=(210-240*f)*Math.PI/180;
 circle(cx,cy,r,'#f8f8f8','#444',1.5);
 c.strokeStyle='#ddd';c.lineWidth=2.5;c.beginPath();c.arc(X(cx),Y(cy),r*.85*scale,-210*Math.PI/180,30*Math.PI/180);c.stroke();
 const colors=[[49,54,149],[69,117,180],[116,173,209],[171,217,233],[224,243,248],[255,255,191],[254,224,144],[253,174,97],[244,109,67],[215,48,39],[165,0,38]];
 const j=Math.min(9,Math.floor(f*10)),mix=f*10-j;
 c.strokeStyle='rgb('+colors[j].map((v,k)=>v+(colors[j+1][k]-v)*mix).join(',')+')';c.beginPath();
 c.arc(X(cx),Y(cy),r*.85*scale,-210*Math.PI/180,-a);c.stroke();
 line(cx,cy,cx+r*.65*Math.cos(a),cy+r*.65*Math.sin(a),'#333',1.2);
 circle(cx,cy,r*.1,'#333');text(label,cx,cy-r*.45,10,'#555');text(fmt(value),cx,cy+r*.45,11,'#222','center',true);
 text(limitLabel(range[0]),cx-.53,cy-.55,9,'#555');text(limitLabel(range[1]),cx+.53,cy-.55,9,'#555');
}

function neutralization(canvas,state,signals={outputs:[]}){
 const p=plot(canvas,0,8.2,0,9.5),{c,X,Y,scale,line,text,circle,rect,arrow}=p;
 const heightRange=signalRange(signals.outputs[2],[0,25]),phRange=signalRange(signals.outputs[3],[0,14]);
 const heightScale=heightRange[1]>0?heightRange[1]:25;
 const h=state?.x?.[2]??14,ph=state?.x?.[3]??7,fh=6.2*clamp(h/heightScale,0,1),t=clamp(ph/14,0,1);
 const col=`rgba(${255*Math.max(0,1-2*t)},${255*(1-2*Math.abs(t-.5))},${255*Math.max(0,2*t-1)},.38)`;
 rect(.56,.86,4.88,Math.max(fh-.06,.02),col,null,.24);
 rect(.56,.86,4.88,Math.max(fh-.06,.02),'rgba(200,228,245,.48)',null,.24);
 rect(.5,.8,5,6.2,null,'#334455',.32,2);
 rect(2.81,7.05,.38,.36,'#ddd','#555',.04);
 line(5.55,1.3,7.1,1.3,'#444',2);arrow(7.4,1.3,7.8,1.3);text('q4',7.8,1.3,12,'#333','left',true);
 gauge(p,6.45,2.13,ph,phRange,'pH');gauge(p,6.45,3.7275,h,heightRange,'h (cm)');
 for(const [x,label,color,value] of [[3.75,'q1','#5faa22',state?.parameters?.q1],[4.95,'q2','#666',state?.parameters?.d],[1.55,'q3','#c20',state?.u?.[0]]]){
  arrow(x,7.7,x,7.1,color);text(label,x+.12,7.5,12,color,'left',true);
  if(Number.isFinite(value))text(fmt(value,1)+' cm³/s',x,7.95,11,color);
 }
 const by=.8+fh*.38,angle=(state?.t||0)*2*Math.PI;
 line(3,7.05,3,by,'#999',2);
 c.beginPath();c.ellipse(X(3),Y(by-.07),.65*.85*scale,.065*scale,0,0,2*Math.PI);c.strokeStyle='#aaa';c.lineWidth=.8;c.stroke();
 for(const offset of [0,Math.PI]){
  const a=angle+offset;line(3,by,3+.65*Math.cos(a),by+.65*.28*Math.sin(a)-.06,'#555',2.5);
 }
 circle(3,by,.07,'#555');
 return [{label:'pH',range:phRange},{label:'h (cm)',range:heightRange}];
}

export const scenes={
 pendulum:{states:2,aspect:'1 / 1',draw:pendulum},
 'dc-motor':{states:3,aspect:'5 / 3',draw:motor,observe(s){omegaMax=Math.max(omegaMax,Math.abs(s.x[1]));currentMax=Math.max(currentMax,Math.abs(s.x[2]));},reset(){omegaMax=1e-3;currentMax=1e-3;}},
 neutralization:{states:4,aspect:'8.2 / 9.5',draw:neutralization}
};

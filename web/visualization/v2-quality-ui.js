/* AAS V2: bounded, strictly display-only dual plots and statistics. */
(() => {
  'use strict';
  const byId=id=>document.getElementById(id);
  const NS='http://www.w3.org/2000/svg';
  const colors={source:'#657da5',processed:'#117a70'};
  const flagColors={filled_outlier:'#d56f22',unfilled_outlier:'#c84132',unfilled_missing:'#c84132',filled_missing:'#df9b19'};
  function svgNode(tag, attributes, text){
    const item=document.createElementNS(NS,tag);
    for(const [key,value] of Object.entries(attributes||{}))item.setAttribute(key,String(value));
    if(text!==undefined)item.textContent=String(text);
    return item;
  }
  function numericTime(value,index){
    const str=String(value??'');
    if(/^\d{4}-\d\d-\d\d/.test(str)){
      const iso=str.replace(' ','T');
      const t=Date.parse(/(Z|[+-]\d{2}:\d{2})$/i.test(iso)?iso:iso+'Z');
      return Number.isFinite(t)?t:index;
    }
    const x=Number(value);
    return Number.isFinite(x)?x:index;
  }
  function draw(svgId,points,field){
    const svg=byId(svgId);svg.replaceChildren();
    const values=points.map(x=>Number.isFinite(x[field])?x[field]:null);
    const finite=values.filter(v=>v!==null);
    if(!finite.length){svg.append(svgNode('text',{x:35,y:90},'本次处理没有有效采样'));return;}
    const xs=points.map((x,i)=>numericTime(x.timestamp,i));
    let minX=Math.min(...xs),maxX=Math.max(...xs);
    if(minX===maxX)maxX=minX+1;
    let minY=Math.min(...finite),maxY=Math.max(...finite);
    const spread=maxY-minY||Math.max(Math.abs(minY)*.05,1);
    minY-=spread*.1;maxY+=spread*.1;
    const fx=i=>40+(xs[i]-minX)/(maxX-minX)*920;
    const fy=y=>166-(y-minY)/(maxY-minY)*133;
    [33,99,166].forEach(y=>svg.append(svgNode('line',{x1:40,y1:y,x2:960,y2:y,stroke:'#e4ebf1','stroke-width':1})));
    let path='',connected=false;
    for(let i=0;i<points.length;i++){
      const v=values[i];
      if(v===null){connected=false;continue;}
      path+=`${connected?'L':'M'}${fx(i).toFixed(2)},${fy(v).toFixed(2)} `;
      connected=true;
    }
    svg.append(svgNode('path',{d:path,stroke:colors[field]}));
    // Visually call out flagged samples (including unfilled gaps at center height).
    points.forEach((point,i)=>{
      if(!flagColors[point.status])return;
      const v=values[i];
      const circle=svgNode('circle',{cx:fx(i),cy:v===null?99:fy(v),r:3.2,fill:flagColors[point.status]});
      circle.append(svgNode('title',{},`${point.timestamp}: ${point.status}\n原值=${point.source??'缺测'}；处理值=${point.processed??'无效'}`));
      svg.append(circle);
    });
    svg.append(svgNode('text',{x:42,y:187},`${points[0].timestamp}`));
    svg.append(svgNode('text',{x:957,y:187,'text-anchor':'end'},`${points[points.length-1].timestamp}`));
    svg.append(svgNode('text',{x:44,y:22},`Y: ${minY.toPrecision(4)} ～ ${maxY.toPrecision(4)}`));
  }
  window.aasV2QualityUI={
    render(result){
      byId('v2PrepReport').hidden=false;
      const stats=result.quality||{},check=result.sample_check||{};
      for(const [field,key] of [['v2QualityTotal','total'],['v2QualityValid','valid_samples'],
        ['v2QualityOutlier','outliers'],['v2QualityMissing','source_missing'],
        ['v2QualityFilled','forward_filled'],['v2QualityInvalid','invalid_samples']]){
        byId(field).textContent=String(stats[key]??0);
      }
      const valid=(stats.valid_rate*100).toFixed(2);
      const message=`有效比例 ${valid}%。训练/评估使用真实有限数值样本；${stats.invalid_samples} 个未填补点保留 NULL 并从模型输入排除。`+
        `当前${result.output_role==='train'?'训练':result.output_role==='test'?'测试':'用途'}预检：${result.output_role==='train'?(check.eligible_for_training?'至少 8 个有效点（仍需运行分组和多参量对齐检查）':'有效点不足 8 个或用途不符'):
          result.output_role==='test'?(check.eligible_for_evaluation?'有有效点（最终仍需标签及多参量对齐校验）':'无有效点'):'请在训练或测试用途下另行核查'}。`;
      byId('v2QualitySampleCheck').textContent=message;
      byId('v2QualitySampleCheck').classList.toggle('warning',!!stats.invalid_samples||!check.eligible_for_training&&result.output_role==='train');
      const series=result.plot_series||[];
      draw('v2PrepRawPlot',series,'source');
      draw('v2PrepCleanPlot',series,'processed');
    },
    clear(){byId('v2PrepReport').hidden=true;},
    async jobs(json){
      const body=byId('v2PrepJobs');
      try{
        const result=await json('/api/v2/telemetry/preprocess/jobs?limit=25');
        body.replaceChildren();
        if(!(result.items||[]).length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.textContent='暂无派生处理记录';tr.append(td);body.append(tr);return;}
        for(const job of result.items){
          const tr=document.createElement('tr');
          const q=job.quality||{};
          const cells=[job.created_utc.slice(0,19).replace('T',' '),`#${job.output_channel_id} · ${job.variable}`,
            job.method,`${q.valid_samples??'?'} / ${q.total??job.output_sample_count}`,String(q.invalid_samples??'?')];
          for(const value of cells){const td=document.createElement('td');td.textContent=value;tr.append(td);}
          const action=document.createElement('td'),link=document.createElement('a');
          link.href='/api/v2/telemetry/preprocess/export?job_id='+encodeURIComponent(job.id);
          link.textContent='导出 CSV';link.setAttribute('download','aas-preprocessed-'+job.id+'.csv');
          action.append(link);tr.append(action);body.append(tr);
        }
      }catch(err){body.replaceChildren();const td=document.createElement('td');td.colSpan=6;td.textContent='记录读取失败：'+err.message;const tr=document.createElement('tr');tr.append(td);body.append(tr);}
    }
  };
})();

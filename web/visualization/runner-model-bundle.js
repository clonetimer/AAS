/* Model file transfer only through same-installation authenticated AAS bundles. */
(() => {'use strict';
const $=id=>document.getElementById(id);
function tell(text,error=false){$('evalModelImportStatus').textContent=text;
 $('evalModelImportStatus').style.color=error?'#b42339':'#31566f';}
function exportChoice(){
 const id=$('evalModel').value,link=$('evalModelExport');
 if(!/^[1-9][0-9]*$/.test(id||'')){link.hidden=true;link.removeAttribute('href');return;}
 link.href='/api/v2/models/export-bundle?model_id='+encodeURIComponent(id);
 link.hidden=false;
 link.textContent='导出当前模型的 AAS 本机认证包';
}
$('evalModel').addEventListener('change',exportChoice);
$('evalModelImport').addEventListener('click',async()=>{
 const button=$('evalModelImport');button.disabled=true;
 try{
  const file=$('evalModelFile').files?.[0],modelName=$('evalModelImportName').value.trim();
  if(!file||!file.name.toLowerCase().endsWith('.aas.zip'))throw Error('仅支持本 AAS 实例导出的 .aas.zip 文件');
  if(!file.size||file.size>75_000_000)throw Error('模型包必须为非空且不超过 75 MB');
  if(!modelName||modelName.length>100)throw Error('请输入 1～100 字的模型名称');
  const bytes=new TextEncoder().encode(modelName);let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
  tell('正在校验模型包的本机签名和所有文件摘要；校验通过后才会装载并发布模型…');
  const response=await fetch('/api/v2/models/import-bundle',{
   method:'POST',headers:{'Content-Type':'application/zip','X-AAS-Model-Name-B64':btoa(binary)},body:file});
  let result;try{result=await response.json();}catch{throw Error('服务器没有返回有效模型导入结果');}
  if(!response.ok)throw Error(result.error||`导入失败 HTTP ${response.status}`);
  tell(`已完成本机签名校验并导入模型：${result.model_name}；正在更新可选模型列表…`);
  if(window.AASRunnerV2?.refreshModels)await window.AASRunnerV2.refreshModels();
  if([...$('evalModel').options].some(option=>String(option.value)===String(result.id))){
   $('evalModel').value=String(result.id);$('evalModel').dispatchEvent(new Event('change'));
   tell('模型已通过认证并可用于当前参量选择的评估。');
  }else tell('模型已导入；但与当前评估参量不一致。请调整测试参量后再选择模型。');
 }catch(error){tell(error.message,true);}finally{button.disabled=false;}
});
})();

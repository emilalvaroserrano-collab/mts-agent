import { NextRequest, NextResponse } from 'next/server';

export const runtime='nodejs';
const PORTAL=(process.env.MTS_API_BASE_URL||'https://mts.192-888-702-801.site').replace(/\/$/,'');
export async function POST(request:NextRequest) {
  const origin=request.headers.get('origin');
  if(origin&&origin!==request.nextUrl.origin)return NextResponse.json({error:'Invalid request origin'},{status:403});
  const authorization=request.headers.get('authorization')||'';
  const customerId=Number(request.headers.get('X-MTS-Customer'));
  if(!/^Bearer csr_[a-zA-Z0-9-]+$/.test(authorization)||!Number.isSafeInteger(customerId)||customerId<=0)return NextResponse.json({error:'A linked portal session is required.'},{status:401});
  const raw=await request.text();
  if(raw.length>64000)return NextResponse.json({error:'Request too large.'},{status:413});
  let body:{resource?:string;method?:string;data?:unknown};
  try{body=JSON.parse(raw);}catch{return NextResponse.json({error:'Invalid request.'},{status:400});}
  const resource=String(body.resource||''),method=body.method||'GET';
  const allowedRead=/^(conversations(?:\/[a-zA-Z0-9-]+\/messages)?(?:\?before=[a-zA-Z0-9:%.+-]+)?|context|notices|push|confirmations\/[a-zA-Z0-9-]+)$/;
  const allowedWrite=/^(confirmations\/[a-zA-Z0-9-]+\/(accept|cancel)|push)$/;
  if(!((method==='GET'&&allowedRead.test(resource))||(method==='POST'&&allowedWrite.test(resource))))return NextResponse.json({error:'Unsupported support action.'},{status:403});
  try {
    const response=await fetch(`${PORTAL}/api/agent/customers/${customerId}/${resource}`,{method,headers:{Authorization:authorization,'Content-Type':'application/json'},body:method==='GET'?undefined:JSON.stringify(body.data||{}),cache:'no-store',redirect:'error',signal:AbortSignal.timeout(15000)});
    return new NextResponse(await response.text(),{status:response.status,headers:{'Content-Type':'application/json','Cache-Control':'no-store'}});
  }catch{return NextResponse.json({error:'Support data could not be reached.'},{status:503});}
}

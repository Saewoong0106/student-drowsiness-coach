import { FaceLandmarker, FilesetResolver } from '/vision/vision_bundle.mjs';

let model;
const indices=[362,385,387,263,373,380,33,160,158,133,153,144];
self.onmessage=async({data})=>{
  if(data.type==='init'){
    try{
      const files=await FilesetResolver.forVisionTasks('/vision/wasm',true);
      model=await FaceLandmarker.createFromOptions(files,{baseOptions:{modelAssetPath:'/vision/face_landmarker.task',delegate:'CPU'},runningMode:'VIDEO',numFaces:1,minFaceDetectionConfidence:.5,minFacePresenceConfidence:.5,minTrackingConfidence:.5,outputFacialTransformationMatrixes:true});
      self.postMessage({type:'ready'});
    }catch{self.postMessage({type:'error',message:'얼굴 측정 모델을 불러오지 못했습니다. 로컬 MediaPipe 설치와 모델 파일을 확인해 주세요.'});}
    return;
  }
  if(data.type==='frame'){
    try{
      const result=model.detectForVideo(data.bitmap,data.timestamp);
      const face=result.faceLandmarks[0];
      const matrix=result.facialTransformationMatrixes[0];
      self.postMessage({type:'measurement',eyes:face?indices.map(i=>[face[i].x,face[i].y]):[],matrix:matrix?Array.from(matrix.data):[],width:data.bitmap.width,height:data.bitmap.height});
    }catch{self.postMessage({type:'error',message:'얼굴 좌표를 측정하지 못했습니다. 카메라와 브라우저를 확인해 주세요.'});}
    finally{data.bitmap.close();}
  }
};

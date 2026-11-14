#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>
#include <vector>
#define CUDA(call) do { cudaError_t e=(call); if(e!=cudaSuccess){std::fprintf(stderr,"%s:%d %s: %s\n",__FILE__,__LINE__,#call,cudaGetErrorString(e));return 1;} } while(0)
__global__ void calculate(int* out,int n){int i=blockIdx.x*blockDim.x+threadIdx.x;if(i<n)out[i]=3*i+7;}
int main(int argc,char**argv){
 int count=0;CUDA(cudaGetDeviceCount(&count));
 int expected=argc>1?std::atoi(argv[1]):count;
 if(count<1 || count!=expected){std::fprintf(stderr,"GPU count %d, expected %d\n",count,expected);return 1;}
 const int n=1<<20;std::vector<int> result(n);
 for(int d=0;d<count;d++){
  CUDA(cudaSetDevice(d));cudaDeviceProp p;CUDA(cudaGetDeviceProperties(&p,d));
  int* out=nullptr;CUDA(cudaMalloc(&out,n*sizeof(int)));
  calculate<<<(n+255)/256,256>>>(out,n);CUDA(cudaGetLastError());CUDA(cudaDeviceSynchronize());
  CUDA(cudaMemcpy(result.data(),out,n*sizeof(int),cudaMemcpyDeviceToHost));CUDA(cudaFree(out));
  for(int i=0;i<n;i++)if(result[i]!=3*i+7){std::fprintf(stderr,"GPU %d mismatch at %d\n",d,i);return 1;}
  std::printf("GPU %d %s: PASS (%d checked values)\n",d,p.name,n);
 }
 return 0;
}

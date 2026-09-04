#include <cstdio>
#include <cublas_v2.h>
#include <cmath>
#include <cuda_runtime.h>
int main(){
    int ver=0; cublasHandle_t h;
    if(cublasCreate(&h)!=CUBLAS_STATUS_SUCCESS){printf("cublasCreate FAILED\n");return 1;}
    cublasGetVersion(h,&ver);
    printf("cuBLAS version: %d\n", ver);
    const int n=512;
    float *A,*B,*C,*dA,*dB,*dC;
    A=(float*)malloc(n*n*4);B=(float*)malloc(n*n*4);C=(float*)malloc(n*n*4);
    for(int i=0;i<n*n;i++){A[i]=1.0f;B[i]=2.0f;}
    cudaMalloc(&dA,n*n*4);cudaMalloc(&dB,n*n*4);cudaMalloc(&dC,n*n*4);
    cudaMemcpy(dA,A,n*n*4,cudaMemcpyHostToDevice);
    cudaMemcpy(dB,B,n*n*4,cudaMemcpyHostToDevice);
    float al=1.0f,be=0.0f;
    cublasSgemm(h,CUBLAS_OP_N,CUBLAS_OP_N,n,n,n,&al,dA,n,dB,n,&be,dC,n);
    cudaDeviceSynchronize();
    cudaMemcpy(C,dC,n*n*4,cudaMemcpyDeviceToHost);
    // each element should be n * 1 * 2 = 1024
    double err=0; for(int i=0;i<n*n;i++) err=fmax(err,fabs(C[i]-2.0*n));
    printf("SGEMM %dx%d: C[0]=%.1f (expected %.1f), max error=%.1f -> %s\n",
           n,n,C[0],2.0*n,err, err==0.0?"PASS":"FAIL");
    cublasDestroy(h); return 0;
}

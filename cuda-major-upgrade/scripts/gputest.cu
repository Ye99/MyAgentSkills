#include <cstdio>
#include <cmath>
#include <cuda_runtime.h>

__global__ void saxpy(int n, float a, const float* x, float* y) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) y[i] = a * x[i] + y[i];
}

int main() {
    int ndev = 0;
    cudaGetDeviceCount(&ndev);
    printf("CUDA runtime version: ");
    int rt = 0, drv = 0;
    cudaRuntimeGetVersion(&rt); cudaDriverGetVersion(&drv);
    printf("%d.%d, driver reports %d.%d\n", rt/1000, (rt%1000)/10, drv/1000, (drv%1000)/10);
    printf("devices: %d\n\n", ndev);

    const int N = 1 << 20;
    for (int d = 0; d < ndev; ++d) {
        cudaSetDevice(d);
        cudaDeviceProp p;
        cudaGetDeviceProperties(&p, d);
        printf("GPU %d: %s  sm_%d%d  %.1f GB  %d SMs\n",
               d, p.name, p.major, p.minor, p.totalGlobalMem/1e9, p.multiProcessorCount);

        float *x, *y, *dx, *dy;
        x = (float*)malloc(N*sizeof(float));
        y = (float*)malloc(N*sizeof(float));
        for (int i = 0; i < N; ++i) { x[i] = 1.0f; y[i] = 2.0f; }
        cudaMalloc(&dx, N*sizeof(float)); cudaMalloc(&dy, N*sizeof(float));
        cudaMemcpy(dx, x, N*sizeof(float), cudaMemcpyHostToDevice);
        cudaMemcpy(dy, y, N*sizeof(float), cudaMemcpyHostToDevice);
        saxpy<<<(N+255)/256, 256>>>(N, 3.0f, dx, dy);
        cudaError_t err = cudaDeviceSynchronize();
        cudaMemcpy(y, dy, N*sizeof(float), cudaMemcpyDeviceToHost);
        double maxerr = 0;
        for (int i = 0; i < N; ++i) maxerr = fmax(maxerr, fabs(y[i] - 5.0f));
        printf("        saxpy kernel: %s, max error = %.1f  -> %s\n\n",
               cudaGetErrorString(err), maxerr, (err==cudaSuccess && maxerr==0.0) ? "PASS" : "FAIL");
        cudaFree(dx); cudaFree(dy); free(x); free(y);
    }
    return 0;
}

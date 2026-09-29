// Diagnostic only: return pixels and DXGI source metadata from ONE acquired frame.
// Does not replace OBS, change official recorder files, or inject user input.
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <stdint.h>
#include <cstring>
#include <new>

struct Capture {
    ID3D11Device *device = nullptr;
    ID3D11DeviceContext *context = nullptr;
    IDXGIOutputDuplication *duplication = nullptr;
    ID3D11Texture2D *staging = nullptr;
    UINT width = 0, height = 0;
};
struct FrameInfo {
    int64_t frequency, before_acquire, after_acquire, last_present, last_mouse, after_copy;
    uint32_t accumulated_frames, protected_content;
};
static_assert(sizeof(FrameInfo) == 56, "Python ABI must match");
template<typename T> static void release(T *&p) { if (p) { p->Release(); p = nullptr; } }
extern "C" __declspec(dllexport) void probe_close(Capture *c) {
    if (!c) return;
    release(c->staging); release(c->duplication); release(c->context); release(c->device);
    delete c;
}
extern "C" __declspec(dllexport) int32_t probe_open(Capture **result, uint32_t *width, uint32_t *height) {
    *result = nullptr;
    IDXGIFactory1 *factory = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), (void **)&factory);
    if (FAILED(hr)) return hr;
    const POINT origin = {0, 0};
    HMONITOR primary = MonitorFromPoint(origin, MONITOR_DEFAULTTOPRIMARY);
    for (UINT ai = 0;; ++ai) {
        IDXGIAdapter1 *adapter = nullptr;
        hr = factory->EnumAdapters1(ai, &adapter);
        if (hr == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(hr)) { release(factory); return hr; }
        for (UINT oi = 0;; ++oi) {
            IDXGIOutput *output = nullptr;
            hr = adapter->EnumOutputs(oi, &output);
            if (hr == DXGI_ERROR_NOT_FOUND) break;
            if (FAILED(hr)) { release(adapter); release(factory); return hr; }
            DXGI_OUTPUT_DESC desc = {};
            hr = output->GetDesc(&desc);
            if (FAILED(hr)) { release(output); release(adapter); release(factory); return hr; }
            if (!desc.AttachedToDesktop || desc.Monitor != primary) { release(output); continue; }
            // This diagnostic refuses rotated sources rather than guessing coordinates.
            if (desc.Rotation != DXGI_MODE_ROTATION_IDENTITY) {
                release(output); release(adapter); release(factory); return E_NOTIMPL;
            }
            Capture *c = new (std::nothrow) Capture();
            if (!c) { release(output); release(adapter); release(factory); return E_OUTOFMEMORY; }
            hr = D3D11CreateDevice(adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0,
                                   D3D11_SDK_VERSION, &c->device, nullptr, &c->context);
            IDXGIOutput1 *output1 = nullptr;
            if (SUCCEEDED(hr)) hr = output->QueryInterface(__uuidof(IDXGIOutput1), (void **)&output1);
            if (SUCCEEDED(hr)) hr = output1->DuplicateOutput(c->device, &c->duplication);
            release(output1); release(output); release(adapter); release(factory);
            if (FAILED(hr)) { probe_close(c); return hr; }
            c->width = desc.DesktopCoordinates.right - desc.DesktopCoordinates.left;
            c->height = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top;
            *width = c->width; *height = c->height; *result = c;
            return S_OK;
        }
        release(adapter);
    }
    release(factory);
    return DXGI_ERROR_NOT_FOUND;
}
// S_OK = image copied, S_FALSE = timeout / pointer-only update. All errors fatal to this probe.
extern "C" __declspec(dllexport) int32_t probe_read(Capture *c, uint32_t x, uint32_t y,
    uint32_t width, uint32_t height, uint8_t *pixels, uint32_t capacity, FrameInfo *info) {
    if (!c || !pixels || !info || !width || !height || width > c->width || height > c->height ||
        x > c->width-width || y > c->height-height || uint64_t(width)*height*4 > capacity) return E_INVALIDARG;
    *info = {};
    LARGE_INTEGER q;
    QueryPerformanceFrequency(&q); info->frequency = q.QuadPart;
    QueryPerformanceCounter(&q); info->before_acquire = q.QuadPart;
    DXGI_OUTDUPL_FRAME_INFO source = {};
    IDXGIResource *resource = nullptr;
    HRESULT hr = c->duplication->AcquireNextFrame(50, &source, &resource);
    QueryPerformanceCounter(&q); info->after_acquire = q.QuadPart;
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) return S_FALSE;
    if (FAILED(hr)) return hr;
    info->last_present = source.LastPresentTime.QuadPart;
    info->last_mouse = source.LastMouseUpdateTime.QuadPart;
    info->accumulated_frames = source.AccumulatedFrames;
    info->protected_content = source.ProtectedContentMaskedOut;
    ID3D11Texture2D *texture = nullptr;
    if (!info->last_present) hr = S_FALSE; // Never substitute a mouse-only timestamp.
    else hr = resource->QueryInterface(__uuidof(ID3D11Texture2D), (void **)&texture);
    if (hr == S_OK) {
        D3D11_TEXTURE2D_DESC td = {};
        texture->GetDesc(&td);
        if (td.Format != DXGI_FORMAT_B8G8R8A8_UNORM || td.Width != c->width || td.Height != c->height)
            hr = E_NOTIMPL;
        if (hr == S_OK && c->staging) {
            D3D11_TEXTURE2D_DESC old = {}; c->staging->GetDesc(&old);
            if (old.Width != width || old.Height != height) release(c->staging);
        }
        if (hr == S_OK && !c->staging) {
            td.Width = width; td.Height = height; td.Usage = D3D11_USAGE_STAGING;
            td.BindFlags = 0; td.CPUAccessFlags = D3D11_CPU_ACCESS_READ; td.MiscFlags = 0;
            hr = c->device->CreateTexture2D(&td, nullptr, &c->staging);
        }
        if (hr == S_OK) {
            D3D11_BOX box = {x, y, 0, x+width, y+height, 1};
            c->context->CopySubresourceRegion(c->staging, 0, 0, 0, 0, texture, 0, &box);
            D3D11_MAPPED_SUBRESOURCE mapped = {};
            hr = c->context->Map(c->staging, 0, D3D11_MAP_READ, 0, &mapped);
            if (hr == S_OK) {
                for (UINT row = 0; row < height; ++row)
                    std::memcpy(pixels+row*width*4, (uint8_t *)mapped.pData+row*mapped.RowPitch, width*4);
                c->context->Unmap(c->staging, 0);
                QueryPerformanceCounter(&q); info->after_copy = q.QuadPart;
            }
        }
    }
    release(texture); release(resource);
    HRESULT released = c->duplication->ReleaseFrame();
    return FAILED(released) ? released : hr;
}

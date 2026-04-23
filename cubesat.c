// pps_host_libusb.c
#include <libusb-1.0/libusb.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <time.h>
#include <errno.h>

#define TX_FILE "/home/cubesat/Cubesat/Proj1/tx/waveforms/Hamming_burst_QPSK_msg_5Mhzfs_3sec.dat"
#define RX_DIR  "/home/cubesat/Cubesat/Proj1/rx/data/iq"

#define SAMPLE_RATE_HZ 5000000
#define CENTER_FREQ_HZ 10000000

#define AMP_ENABLE 0
#define ANTENNA_ENABLE 0
#define LNA_GAIN 16
#define VGA_GAIN 20
#define TXVGA_GAIN 20

#define CHUNK_SIZE 0x4000
#define READ_TIMEOUT_MS 20
#define WRITE_TIMEOUT_MS 20
#define STATUS_PRINT_SEC 2

#define VID 0x1D50
#define PID 0x6089

#define EP_IN  0x81
#define EP_OUT 0x02

#define VENDOR_OUT 0x40
#define VENDOR_IN  0xC0

#define HACKRF_VENDOR_REQUEST_SET_TRANSCEIVER_MODE 1
#define HACKRF_VENDOR_REQUEST_SAMPLE_RATE_SET      6
#define HACKRF_VENDOR_REQUEST_BASEBAND_FILTER_BW   7
#define HACKRF_VENDOR_REQUEST_SET_FREQ             16
#define HACKRF_VENDOR_REQUEST_AMP_ENABLE           17
#define HACKRF_VENDOR_REQUEST_SET_LNA_GAIN         19
#define HACKRF_VENDOR_REQUEST_SET_VGA_GAIN         20
#define HACKRF_VENDOR_REQUEST_SET_TXVGA_GAIN       21
#define HACKRF_VENDOR_REQUEST_ANTENNA_ENABLE       23

#define TRANSCEIVER_MODE_OFF 0

static volatile sig_atomic_t running = 1;

static pthread_mutex_t tx_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t rx_lock = PTHREAD_MUTEX_INITIALIZER;

static uint64_t tx_bytes = 0;
static uint64_t rx_bytes = 0;

typedef struct {
    libusb_device_handle *dev;
    FILE *tx_fp;
} tx_ctx_t;

typedef struct {
    libusb_device_handle *dev;
    FILE *rx_fp;
} rx_ctx_t;

static void log_msg(const char *msg) {
    printf("%s\n", msg);
    fflush(stdout);
}

static int ctrl_out(libusb_device_handle *dev,
                    uint8_t request,
                    uint16_t value,
                    uint16_t index,
                    const unsigned char *data,
                    uint16_t length)
{
    return libusb_control_transfer(
        dev,
        VENDOR_OUT,
        request,
        value,
        index,
        (unsigned char *)data,
        length,
        1000
    );
}

static int ctrl_in(libusb_device_handle *dev,
                   uint8_t request,
                   uint16_t value,
                   uint16_t index,
                   unsigned char *data,
                   uint16_t length)
{
    return libusb_control_transfer(
        dev,
        VENDOR_IN,
        request,
        value,
        index,
        data,
        length,
        1000
    );
}

static int set_mode(libusb_device_handle *dev, uint8_t mode) {
    return ctrl_out(dev, HACKRF_VENDOR_REQUEST_SET_TRANSCEIVER_MODE, mode, 0, NULL, 0);
}

static int set_freq(libusb_device_handle *dev, uint32_t freq_hz) {
    uint32_t mhz = freq_hz / 1000000;
    uint32_t hz  = freq_hz % 1000000;
    unsigned char payload[8];

    memcpy(payload, &mhz, 4);
    memcpy(payload + 4, &hz, 4);

    return ctrl_out(dev, HACKRF_VENDOR_REQUEST_SET_FREQ, 0, 0, payload, sizeof(payload));
}

static int set_sample_rate(libusb_device_handle *dev, uint32_t freq_hz, uint32_t divider) {
    unsigned char payload[8];
    memcpy(payload, &freq_hz, 4);
    memcpy(payload + 4, &divider, 4);
    return ctrl_out(dev, HACKRF_VENDOR_REQUEST_SAMPLE_RATE_SET, 0, 0, payload, sizeof(payload));
}

static int set_baseband_filter_bw(libusb_device_handle *dev, uint32_t bw_hz) {
    uint16_t value = bw_hz & 0xFFFF;
    uint16_t index = (bw_hz >> 16) & 0xFFFF;
    return ctrl_out(dev, HACKRF_VENDOR_REQUEST_BASEBAND_FILTER_BW, value, index, NULL, 0);
}

static int set_amp_enable(libusb_device_handle *dev, bool enabled) {
    return ctrl_out(dev, HACKRF_VENDOR_REQUEST_AMP_ENABLE, enabled ? 1 : 0, 0, NULL, 0);
}

static int set_antenna_enable(libusb_device_handle *dev, bool enabled) {
    return ctrl_out(dev, HACKRF_VENDOR_REQUEST_ANTENNA_ENABLE, enabled ? 1 : 0, 0, NULL, 0);
}

static int set_lna_gain(libusb_device_handle *dev, uint16_t gain) {
    unsigned char resp[1];
    int ret = ctrl_in(dev, HACKRF_VENDOR_REQUEST_SET_LNA_GAIN, 0, gain, resp, 1);
    return ret < 0 ? ret : 0;
}

static int set_vga_gain(libusb_device_handle *dev, uint16_t gain) {
    unsigned char resp[1];
    int ret = ctrl_in(dev, HACKRF_VENDOR_REQUEST_SET_VGA_GAIN, 0, gain, resp, 1);
    return ret < 0 ? ret : 0;
}

static int set_txvga_gain(libusb_device_handle *dev, uint16_t gain) {
    unsigned char resp[1];
    int ret = ctrl_in(dev, HACKRF_VENDOR_REQUEST_SET_TXVGA_GAIN, 0, gain, resp, 1);
    return ret < 0 ? ret : 0;
}

static int configure_device(libusb_device_handle *dev) {
    int ret;

    ret = set_mode(dev, TRANSCEIVER_MODE_OFF);
    if (ret < 0) return ret;

    ret = set_sample_rate(dev, SAMPLE_RATE_HZ, 1);
    if (ret < 0) return ret;

    ret = set_baseband_filter_bw(dev, 5000000);
    if (ret < 0) return ret;

    ret = set_freq(dev, CENTER_FREQ_HZ);
    if (ret < 0) return ret;

    ret = set_amp_enable(dev, AMP_ENABLE);
    if (ret < 0) return ret;

    ret = set_antenna_enable(dev, ANTENNA_ENABLE);
    if (ret < 0) return ret;

    ret = set_lna_gain(dev, LNA_GAIN);
    if (ret < 0) return ret;

    ret = set_vga_gain(dev, VGA_GAIN);
    if (ret < 0) return ret;

    ret = set_txvga_gain(dev, TXVGA_GAIN);
    if (ret < 0) return ret;

    ret = set_mode(dev, TRANSCEIVER_MODE_OFF);
    if (ret < 0) return ret;

    return 0;
}

static void make_rx_path(char *out, size_t out_sz) {
    time_t now = time(NULL);
    struct tm tm_now;
    localtime_r(&now, &tm_now);

    mkdir(RX_DIR, 0777);

    snprintf(out, out_sz,
             RX_DIR "/pps_rx_%04d%02d%02d_%02d%02d%02d.dat",
             tm_now.tm_year + 1900,
             tm_now.tm_mon + 1,
             tm_now.tm_mday,
             tm_now.tm_hour,
             tm_now.tm_min,
             tm_now.tm_sec);
}

static void *tx_worker(void *arg) {
    tx_ctx_t *ctx = (tx_ctx_t *)arg;
    unsigned char buf[CHUNK_SIZE];

    while (running) {
        size_t got = fread(buf, 1, CHUNK_SIZE, ctx->tx_fp);

        if (got == 0) {
            fseek(ctx->tx_fp, 0, SEEK_SET);
            got = fread(buf, 1, CHUNK_SIZE, ctx->tx_fp);
            if (got == 0) {
                usleep(10000);
                continue;
            }
        }

        if (got < CHUNK_SIZE) {
            fseek(ctx->tx_fp, 0, SEEK_SET);
            size_t remain = CHUNK_SIZE - got;
            size_t got2 = fread(buf + got, 1, remain, ctx->tx_fp);
            got += got2;

            if (got < CHUNK_SIZE) {
                memset(buf + got, 0, CHUNK_SIZE - got);
                got = CHUNK_SIZE;
            }
        }

        int transferred = 0;
        int ret = libusb_bulk_transfer(
            ctx->dev,
            EP_OUT,
            buf,
            CHUNK_SIZE,
            &transferred,
            WRITE_TIMEOUT_MS
        );

        if (ret == 0) {
            pthread_mutex_lock(&tx_lock);
            tx_bytes += (uint64_t)transferred;
            pthread_mutex_unlock(&tx_lock);
        } else if (ret == LIBUSB_ERROR_TIMEOUT) {
            // Normal when firmware is not in TX mode.
            continue;
        } else {
            fprintf(stderr, "[TX] USB error: %s\n", libusb_error_name(ret));
            running = 0;
            return NULL;
        }
    }

    return NULL;
}

static void *rx_worker(void *arg) {
    rx_ctx_t *ctx = (rx_ctx_t *)arg;
    unsigned char buf[CHUNK_SIZE];

    while (running) {
        int transferred = 0;
        int ret = libusb_bulk_transfer(
            ctx->dev,
            EP_IN,
            buf,
            CHUNK_SIZE,
            &transferred,
            READ_TIMEOUT_MS
        );

        if (ret == 0) {
            if (transferred > 0) {
                fwrite(buf, 1, transferred, ctx->rx_fp);
                pthread_mutex_lock(&rx_lock);
                rx_bytes += (uint64_t)transferred;
                pthread_mutex_unlock(&rx_lock);
            }
        } else if (ret == LIBUSB_ERROR_TIMEOUT) {
            // Normal when firmware is not in RX mode.
            continue;
        } else {
            fprintf(stderr, "[RX] USB error: %s\n", libusb_error_name(ret));
            running = 0;
            return NULL;
        }
    }

    return NULL;
}

static void *status_worker(void *arg) {
    (void)arg;

    uint64_t last_tx = 0;
    uint64_t last_rx = 0;

    while (running) {
        sleep(STATUS_PRINT_SEC);

        pthread_mutex_lock(&tx_lock);
        uint64_t cur_tx = tx_bytes;
        pthread_mutex_unlock(&tx_lock);

        pthread_mutex_lock(&rx_lock);
        uint64_t cur_rx = rx_bytes;
        pthread_mutex_unlock(&rx_lock);

        uint64_t dtx = cur_tx - last_tx;
        uint64_t drx = cur_rx - last_rx;
        last_tx = cur_tx;
        last_rx = cur_rx;

        printf("[STATUS] total_tx=%llu B total_rx=%llu B | tx_rate=%.1f B/s | rx_rate=%.1f B/s\n",
               (unsigned long long)cur_tx,
               (unsigned long long)cur_rx,
               (double)dtx / STATUS_PRINT_SEC,
               (double)drx / STATUS_PRINT_SEC);
        fflush(stdout);
    }

    return NULL;
}

static void handle_signal(int sig) {
    running = 0;
    fprintf(stderr, "\nStopping on signal %d...\n", sig);
}

int main(void) {
    int ret;
    libusb_context *usb_ctx = NULL;
    libusb_device_handle *dev = NULL;

    if (access(TX_FILE, F_OK) != 0) {
        fprintf(stderr, "TX file not found: %s\n", TX_FILE);
        return 1;
    }

    char rx_path[512];
    make_rx_path(rx_path, sizeof(rx_path));

    printf("TX file:      %s\n", TX_FILE);
    printf("RX output:    %s\n", rx_path);
    printf("Sample rate:  %d Hz\n", SAMPLE_RATE_HZ);
    printf("Center freq:  %d Hz\n", CENTER_FREQ_HZ);

    ret = libusb_init(&usb_ctx);
    if (ret < 0) {
        fprintf(stderr, "libusb_init failed: %s\n", libusb_error_name(ret));
        return 1;
    }

    dev = libusb_open_device_with_vid_pid(usb_ctx, VID, PID);
    if (!dev) {
        fprintf(stderr, "HackRF not found\n");
        libusb_exit(usb_ctx);
        return 1;
    }

    if (libusb_kernel_driver_active(dev, 0) == 1) {
        libusb_detach_kernel_driver(dev, 0);
    }

    ret = libusb_claim_interface(dev, 0);
    if (ret < 0) {
        fprintf(stderr, "claim_interface failed: %s\n", libusb_error_name(ret));
        libusb_close(dev);
        libusb_exit(usb_ctx);
        return 1;
    }

    ret = configure_device(dev);
    if (ret < 0) {
        fprintf(stderr, "configure_device failed: %s\n", libusb_error_name(ret));
        libusb_release_interface(dev, 0);
        libusb_close(dev);
        libusb_exit(usb_ctx);
        return 1;
    }

    log_msg("HackRF configured. Firmware remains in OFF mode until PPS scheduler switches modes.");

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);

    FILE *tx_fp = fopen(TX_FILE, "rb");
    if (!tx_fp) {
        perror("fopen TX_FILE");
        libusb_release_interface(dev, 0);
        libusb_close(dev);
        libusb_exit(usb_ctx);
        return 1;
    }

    FILE *rx_fp = fopen(rx_path, "wb");
    if (!rx_fp) {
        perror("fopen RX output");
        fclose(tx_fp);
        libusb_release_interface(dev, 0);
        libusb_close(dev);
        libusb_exit(usb_ctx);
        return 1;
    }

    tx_ctx_t tx_ctx = { .dev = dev, .tx_fp = tx_fp };
    rx_ctx_t rx_ctx = { .dev = dev, .rx_fp = rx_fp };

    pthread_t tx_thread, rx_thread, st_thread;

    pthread_create(&tx_thread, NULL, tx_worker, &tx_ctx);
    pthread_create(&rx_thread, NULL, rx_worker, &rx_ctx);
    pthread_create(&st_thread, NULL, status_worker, NULL);

    while (running) {
        usleep(200000);
        fflush(rx_fp);
    }

    pthread_join(tx_thread, NULL);
    pthread_join(rx_thread, NULL);
    pthread_join(st_thread, NULL);

    set_mode(dev, TRANSCEIVER_MODE_OFF);

    fclose(tx_fp);
    fclose(rx_fp);

    libusb_release_interface(dev, 0);
    libusb_close(dev);
    libusb_exit(usb_ctx);

    log_msg("Done.");
    return 0;
}
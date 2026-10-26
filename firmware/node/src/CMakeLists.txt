idf_component_register(
    SRCS "main.c" "audio.c" "doa.c" "fft.c" "link.c" "config.c"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_i2s esp_driver_usb_serial_jtag nvs_flash esp_timer
)

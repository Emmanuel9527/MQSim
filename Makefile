CC        := g++
LD        := g++
CC_FLAGS := -std=c++11 -O3 -g

MODULES   := exec host nvm_chip nvm_chip/flash_memory sim ssd utils
SRC_DIR   := $(addprefix src/,$(MODULES)) src
BUILD_DIR := $(addprefix build/,$(MODULES)) build

SRC       := $(foreach sdir,$(SRC_DIR),$(wildcard $(sdir)/*.cpp))
SRC       := src/main.cpp $(SRC)
OBJ       := $(patsubst src/%.cpp,build/%.o,$(SRC))
LIB_SRC   := $(filter-out src/main.cpp,$(SRC))
LIB_OBJ   := $(patsubst src/%.cpp,build/%.o,$(LIB_SRC))
INCLUDES  := $(addprefix -I,$(SRC_DIR))

vpath %.cpp $(SRC_DIR)

define make-goal
$1/%.o: %.cpp
	$(CC) $(CC_FLAGS) $(INCLUDES) -c $$< -o $$@
endef

.PHONY: all checkdirs clean

all: checkdirs MQSim MQSimIPCDaemon

MQSim: $(OBJ)
	$(LD) $^ -o $@

MQSimIPCDaemon: mqsim_ipc_daemon.cpp mqsim_native_online_model.cpp mqsim_native_online_model.h vuc_service.cpp vuc_service.h $(LIB_OBJ)
	$(CC) $(CC_FLAGS) $(INCLUDES) -I../nvmevirt mqsim_ipc_daemon.cpp mqsim_native_online_model.cpp vuc_service.cpp $(LIB_OBJ) -o $@

# Side-by-side build for the widened 15-page VUC reply; useful while an
# older daemon binary is still running and holding MQSimIPCDaemon open.
MQSimIPCDaemonV2: mqsim_ipc_daemon.cpp mqsim_native_online_model.cpp mqsim_native_online_model.h vuc_service.cpp vuc_service.h $(LIB_OBJ)
	$(CC) $(CC_FLAGS) $(INCLUDES) -I../nvmevirt mqsim_ipc_daemon.cpp mqsim_native_online_model.cpp vuc_service.cpp $(LIB_OBJ) -o $@

checkdirs: $(BUILD_DIR)

$(BUILD_DIR):
	mkdir -p $@

clean:
	rm -rf $(BUILD_DIR)
	rm -f MQSim
	rm -f MQSimIPCDaemon
	rm -f MQSimIPCDaemonV2
	rm -f VUCTimingSmoke
	rm -f VUCV2Benchmark

$(foreach bdir,$(BUILD_DIR),$(eval $(call make-goal,$(bdir))))

# Run with scripts/vuc/test_timing_model.py; no NVMe hardware is needed.
VUCTimingSmoke: tests/vuc_timing_smoke.cpp vuc_service.cpp vuc_service.h mqsim_native_online_model.cpp $(LIB_OBJ)
	$(CC) $(CC_FLAGS) $(INCLUDES) tests/vuc_timing_smoke.cpp vuc_service.cpp mqsim_native_online_model.cpp $(LIB_OBJ) -o $@

# Independent host project must be built first (CMake). No socket transport.
VUCANN_DIR ?= ../../vucann
VUCV2Benchmark: tests/vuc_v2_benchmark.cpp vuc_service.cpp vuc_service.h mqsim_native_online_model.cpp $(VUCANN_DIR)/build/libvucann_core.a $(LIB_OBJ)
	$(CC) $(CC_FLAGS) -std=c++17 $(INCLUDES) -I$(VUCANN_DIR)/include tests/vuc_v2_benchmark.cpp vuc_service.cpp mqsim_native_online_model.cpp $(VUCANN_DIR)/build/libvucann_core.a $(LIB_OBJ) -o $@

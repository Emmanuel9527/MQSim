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

MQSimIPCDaemon: mqsim_ipc_daemon.cpp mqsim_native_online_model.cpp mqsim_native_online_model.h $(LIB_OBJ)
	$(CC) $(CC_FLAGS) $(INCLUDES) -I../nvmevirt mqsim_ipc_daemon.cpp mqsim_native_online_model.cpp $(LIB_OBJ) -o $@

checkdirs: $(BUILD_DIR)

$(BUILD_DIR):
	mkdir -p $@

clean:
	rm -rf $(BUILD_DIR)
	rm -f MQSim
	rm -f MQSimIPCDaemon

$(foreach bdir,$(BUILD_DIR),$(eval $(call make-goal,$(bdir))))

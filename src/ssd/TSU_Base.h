#ifndef TSU_H
#define TSU_H

#include <list>
#include "../sim/Sim_Defs.h"
#include "../sim/Sim_Object.h"
#include "../nvm_chip/flash_memory/Flash_Chip.h"
#include "../sim/Sim_Reporter.h"
#include "FTL.h"
#include "NVM_PHY_ONFI_NVDDR2.h"
#include "Flash_Transaction_Queue.h"

namespace SSD_Components
{
enum class Flash_Scheduling_Type
{
	OUT_OF_ORDER,
	PRIORITY_OUT_OF_ORDER,
	FLIN
};
// How the TSU dispatches commands to the dies of one chip (Die_Dispatch_Mode device parameter).
// ONE_DIE: one die per dispatch and a new dispatch only when the whole chip is idle (behavior since e338620, default).
// ALL_DIES: one dispatch sends a command to every die that has ready work (die-interleaved, pre-e338620 behavior);
//           the next dispatch still waits until the whole chip is idle.
// INDEPENDENT_DIES: a free die can receive a read while other dies of the same chip are still reading
//           (ONFI multi-LUN operation). Only implemented for PRIORITY_OUT_OF_ORDER.
enum class Die_Dispatch_Mode
{
	ONE_DIE,
	ALL_DIES,
	INDEPENDENT_DIES
};
class FTL;
class TSU_Base : public MQSimEngine::Sim_Object
{
public:
	TSU_Base(const sim_object_id_type &id, FTL *ftl, NVM_PHY_ONFI_NVDDR2 *NVMController, Flash_Scheduling_Type Type,
			 unsigned int Channel_no, unsigned int chip_no_per_channel, unsigned int DieNoPerChip, unsigned int PlaneNoPerDie,
			 bool EraseSuspensionEnabled, bool ProgramSuspensionEnabled,
			 sim_time_type WriteReasonableSuspensionTimeForRead,
			 sim_time_type EraseReasonableSuspensionTimeForRead,
			 sim_time_type EraseReasonableSuspensionTimeForWrite);
	virtual ~TSU_Base();
	void Setup_triggers();

	/*When an MQSim needs to send a set of transactions for execution, the following 
		* three funcitons should be invoked in this order:
		* Prepare_for_transaction_submit()
		* Submit_transaction(transaction)
		* .....
		* Submit_transaction(transaction)
		* Schedule()
		*
		* The above mentioned mechanism helps to exploit die-level and plane-level parallelism.
		* More precisely, first the transactions are queued and then, when the Schedule function
		* is invoked, the TSU has that opportunity to schedule them together to exploit multiplane
		* and die-interleaved execution.
		*/
	void Prepare_for_transaction_submit()
	{
		opened_scheduling_reqs++;
		if (opened_scheduling_reqs > 1)
		{
			return;
		}
		transaction_receive_slots.clear();
	}

	void Submit_transaction(NVM_Transaction_Flash *transaction)
	{
		transaction_receive_slots.push_back(transaction);
	}

	/* Shedules the transactions currently stored in inputTransactionSlots. The transactions could
		* be mixes of reads, writes, and erases.
		*/
	virtual void Schedule() = 0;
	virtual void Report_results_in_XML(std::string name_prefix, Utils::XmlWriter &xmlwriter);

	Die_Dispatch_Mode die_dispatch_mode = Die_Dispatch_Mode::ONE_DIE;
	// true: a multi-plane read may combine different page offsets in different planes of one die
	// (independent/asynchronous multi-plane read). false: identical page offset required (MQSim default).
	bool multiplane_read_any_page = false;

protected:
	FTL *ftl;
	NVM_PHY_ONFI_NVDDR2 *_NVMController;
	Flash_Scheduling_Type type;
	unsigned int channel_count;
	unsigned int chip_no_per_channel;
	unsigned int die_no_per_chip;
	unsigned int plane_no_per_die;
	bool eraseSuspensionEnabled, programSuspensionEnabled;
	sim_time_type writeReasonableSuspensionTimeForRead;
	sim_time_type eraseReasonableSuspensionTimeForRead; //the time period
	sim_time_type eraseReasonableSuspensionTimeForWrite;
	flash_chip_ID_type *Round_robin_turn_of_channel; //Used for round-robin service of the chips in channels

	static TSU_Base *_my_instance;
	std::list<NVM_Transaction_Flash *> transaction_receive_slots;  //Stores the transactions that are received for sheduling
	std::list<NVM_Transaction_Flash *> transaction_dispatch_slots; //Used to submit transactions to the channel controller
	virtual bool service_read_transaction(NVM::FlashMemory::Flash_Chip *chip) = 0;
	virtual bool service_write_transaction(NVM::FlashMemory::Flash_Chip *chip) = 0;
	virtual bool service_erase_transaction(NVM::FlashMemory::Flash_Chip *chip) = 0;
	bool issue_command_to_chip(Flash_Transaction_Queue *sourceQueue1, Flash_Transaction_Queue *sourceQueue2, Transaction_Type transactionType, bool suspensionRequired);
	static void handle_transaction_serviced_signal_from_PHY(NVM_Transaction_Flash *transaction);
	static void handle_channel_idle_signal(flash_channel_ID_type);
	static void handle_chip_idle_signal(NVM::FlashMemory::Flash_Chip *chip);
	int opened_scheduling_reqs;
	void process_chip_requests(NVM::FlashMemory::Flash_Chip* chip)
	{
		if (!_my_instance->service_read_transaction(chip)) {
			if (!_my_instance->service_write_transaction(chip)) {
				_my_instance->service_erase_transaction(chip);
			}
		}
	}

private:
	bool transaction_is_ready(NVM_Transaction_Flash* transaction)
	{
		switch (transaction->Type)
		{
		case Transaction_Type::READ:
			return true;
		case Transaction_Type::WRITE:
			return static_cast<NVM_Transaction_Flash_WR*>(transaction)->RelatedRead == NULL;
		case Transaction_Type::ERASE:
			return static_cast<NVM_Transaction_Flash_ER*>(transaction)->Page_movement_activities.size() == 0;
		default:
			return true;
		}
	}
};
} // namespace SSD_Components

#endif //TSU_H

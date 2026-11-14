#include <string>
#include "TSU_Base.h"

#define TRTOSTR(TR) (TR->Type == Transaction_Type::READ ? "Read, " : (TR->Type == Transaction_Type::WRITE ? "Write, " : "Erase, ") )

namespace SSD_Components
{
	TSU_Base* TSU_Base::_my_instance = NULL;

	TSU_Base::TSU_Base(const sim_object_id_type& id, FTL* ftl, NVM_PHY_ONFI_NVDDR2* NVMController, Flash_Scheduling_Type Type,
		unsigned int ChannelCount, unsigned int chip_no_per_channel, unsigned int DieNoPerChip, unsigned int PlaneNoPerDie,
		bool EraseSuspensionEnabled, bool ProgramSuspensionEnabled,
		sim_time_type WriteReasonableSuspensionTimeForRead,
		sim_time_type EraseReasonableSuspensionTimeForRead,
		sim_time_type EraseReasonableSuspensionTimeForWrite)
		: Sim_Object(id), ftl(ftl), _NVMController(NVMController), type(Type),
		channel_count(ChannelCount), chip_no_per_channel(chip_no_per_channel), die_no_per_chip(DieNoPerChip), plane_no_per_die(PlaneNoPerDie),
		eraseSuspensionEnabled(EraseSuspensionEnabled), programSuspensionEnabled(ProgramSuspensionEnabled),
		writeReasonableSuspensionTimeForRead(WriteReasonableSuspensionTimeForRead), eraseReasonableSuspensionTimeForRead(EraseReasonableSuspensionTimeForRead),
		eraseReasonableSuspensionTimeForWrite(EraseReasonableSuspensionTimeForWrite), opened_scheduling_reqs(0)
	{
		_my_instance = this;
		Round_robin_turn_of_channel = new flash_chip_ID_type[channel_count];
		for (unsigned int channelID = 0; channelID < channel_count; channelID++) {
			Round_robin_turn_of_channel[channelID] = 0;
		}
	}

	TSU_Base::~TSU_Base()
	{
		delete[] Round_robin_turn_of_channel;
	}

	void TSU_Base::Setup_triggers()
	{
		Sim_Object::Setup_triggers();
		_NVMController->ConnectToTransactionServicedSignal(handle_transaction_serviced_signal_from_PHY);
		_NVMController->ConnectToChannelIdleSignal(handle_channel_idle_signal);
		_NVMController->ConnectToChipIdleSignal(handle_chip_idle_signal);
	}

	void TSU_Base::handle_transaction_serviced_signal_from_PHY(NVM_Transaction_Flash* transaction)
	{
		//TSU does nothing. The generator of the transaction will handle it.
	}

	void TSU_Base::handle_channel_idle_signal(flash_channel_ID_type channelID)
	{
		for (unsigned int i = 0; i < _my_instance->chip_no_per_channel; i++) {
			//The TSU does not check if the chip is idle or not since it is possible to suspend a busy chip and issue a new command
			_my_instance->process_chip_requests(_my_instance->_NVMController->Get_chip(channelID, _my_instance->Round_robin_turn_of_channel[channelID]));
			_my_instance->Round_robin_turn_of_channel[channelID] = (flash_chip_ID_type)(_my_instance->Round_robin_turn_of_channel[channelID] + 1) % _my_instance->chip_no_per_channel;

			//A transaction has been started, so TSU should stop searching for another chip
			if (_my_instance->_NVMController->Get_channel_status(channelID) == BusChannelStatus::BUSY) {
				break;
			}
		}
	}
	
	void TSU_Base::handle_chip_idle_signal(NVM::FlashMemory::Flash_Chip* chip)
	{
		if (_my_instance->_NVMController->Get_channel_status(chip->ChannelID) == BusChannelStatus::IDLE) {
			_my_instance->process_chip_requests(chip);
		}
	}

	void TSU_Base::Report_results_in_XML(std::string name_prefix, Utils::XmlWriter& xmlwriter)
	{
	}

	bool TSU_Base::issue_command_to_chip(Flash_Transaction_Queue *sourceQueue1, Flash_Transaction_Queue *sourceQueue2, Transaction_Type transactionType, bool suspensionRequired)
	{
		const bool legacy = die_dispatch_mode == Die_Dispatch_Mode::ONE_DIE;
		NVM::FlashMemory::Flash_Chip* chip = _NVMController->Get_chip(sourceQueue1->front()->Address.ChannelID, sourceQueue1->front()->Address.ChipID);
		flash_die_ID_type dieID = sourceQueue1->front()->Address.DieID;
		flash_page_ID_type pageID = sourceQueue1->front()->Address.PageID;
		unsigned int planeVector = 0;
		bool issued = false;
		static int issueCntr = 0;

		for (unsigned int i = 0; i < die_no_per_chip; i++)
		{
			transaction_dispatch_slots.clear();
			planeVector = 0;
			if (die_dispatch_mode == Die_Dispatch_Mode::INDEPENDENT_DIES && !_NVMController->Is_die_free(chip, dieID))
			{
				dieID = (dieID + 1) % die_no_per_chip;
				continue;
			}

			Flash_Transaction_Queue* queues[2] = { sourceQueue1, sourceQueue2 };
			for (int q = 0; q < 2; q++)
			{
				Flash_Transaction_Queue* queue = queues[q];
				if (queue == NULL || (q == 1 && transaction_dispatch_slots.size() >= plane_no_per_die))
					continue;
				for (Flash_Transaction_Queue::iterator it = queue->begin(); it != queue->end();)
				{
					if (transaction_is_ready(*it) && (*it)->Address.DieID == dieID && !(planeVector & 1 << (*it)->Address.PlaneID))
					{
						//Multi-plane command: identical page offsets unless independent multi-plane reads are enabled.
						//The non-legacy modes compare against the first transaction chosen for this die.
						if (!legacy && planeVector == 0)
							pageID = (*it)->Address.PageID;
						if (planeVector == 0 || (*it)->Address.PageID == pageID
							|| (multiplane_read_any_page && (*it)->Type == Transaction_Type::READ))
						{
							(*it)->SuspendRequired = suspensionRequired;
							planeVector |= 1 << (*it)->Address.PlaneID;
							transaction_dispatch_slots.push_back(*it);
							DEBUG(issueCntr++ << ": " << Simulator->Time() <<" Issueing Transaction - Type:" << TRTOSTR((*it)) << ", PPA:" << (*it)->PPA << ", LPA:" << (*it)->LPA << ", Channel: " << (*it)->Address.ChannelID << ", Chip: " << (*it)->Address.ChipID);
							queue->remove(it++);
							continue;
						}
					}
					it++;
				}
			}

			bool sent = transaction_dispatch_slots.size() > 0;
			if (sent)
			{
				_NVMController->Send_command_to_chip(transaction_dispatch_slots);
				issued = true;
			}
			transaction_dispatch_slots.clear();
			dieID = (dieID + 1) % die_no_per_chip;
			if (legacy)
				return sent;
		}

		return issued;
	}
}

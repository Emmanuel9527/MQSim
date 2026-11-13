#include "../src/ssd/Channel_Transfer_Time.h"
#include <cassert>
#include <stdexcept>
int main(){using SSD_Components::Channel_transfer_ns;
assert(Channel_transfer_ns(4096,1,333)==12301);
assert(Channel_transfer_ns(4096,1,2400)==1707);
assert(Channel_transfer_ns(4096,1,500)==8192);
assert(Channel_transfer_ns(1,1,2400)==1);
assert(Channel_transfer_ns(3,1,1000)==4);
assert(Channel_transfer_ns(0,1,2400)==0);
for(unsigned b=1;b<20000;b+=17){auto a=Channel_transfer_ns(b,1,333),c=Channel_transfer_ns(b,1,2400);assert(c>0&&a>=c);assert(Channel_transfer_ns(b+1,1,2400)>=c);}
bool threw=false;try{Channel_transfer_ns(4096,1,0);}catch(const std::invalid_argument&){threw=true;}assert(threw);
}

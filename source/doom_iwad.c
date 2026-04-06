#include "doom_iwad.h"
#include "lprintf.h"

#ifdef NUMWORKS
#include <eadk.h>
#endif

#if defined(GBADOOM_VERBOSE_IWAD_LOGS) && GBADOOM_VERBOSE_IWAD_LOGS
#define IWAD_LOG(...) lprintf(LO_ALWAYS, __VA_ARGS__)
#else
#define IWAD_LOG(...) ((void)0)
#endif

static void DoomIwadLogSelection(const char* source, unsigned int size)
{
#if defined(GBADOOM_VERBOSE_IWAD_LOGS) && GBADOOM_VERBOSE_IWAD_LOGS
	static int s_logged = 0;
	if (!s_logged)
	{
		IWAD_LOG("[IWAD] Source=%s size=%u", source, size);
		s_logged = 1;
	}
#else
	(void)source;
	(void)size;
#endif
}

#if defined(NUMWORKS)

#ifndef USE_EXTERNAL_IWAD
#define USE_EXTERNAL_IWAD 0
#endif

#if USE_EXTERNAL_IWAD
static int NumWorksExternalIwadAvailable(void)
{
	const int available = eadk_external_data_size >= 4 &&
		   eadk_external_data[0] == 'I' &&
		   eadk_external_data[1] == 'W' &&
		   eadk_external_data[2] == 'A' &&
		   eadk_external_data[3] == 'D';

#if defined(GBADOOM_VERBOSE_IWAD_LOGS) && GBADOOM_VERBOSE_IWAD_LOGS
	static int s_logged = 0;
	if (!s_logged)
	{
		IWAD_LOG("[IWAD] External data %s (size=%u)",
				 available ? "detected" : "missing/invalid",
				 (unsigned int)eadk_external_data_size);
		s_logged = 1;
	}
#endif

	return available;
}
#endif

#endif

#if defined(NUMWORKS) && PLATFORM_DEVICE && USE_EXTERNAL_IWAD

const unsigned char* doom_iwad_data(void)
{
	if (NumWorksExternalIwadAvailable())
	{
		DoomIwadLogSelection("external", (unsigned int)eadk_external_data_size);
		return (const unsigned char*)eadk_external_data;
	}

	DoomIwadLogSelection("none", 0);
	return 0;
}

unsigned int doom_iwad_size(void)
{
	if (NumWorksExternalIwadAvailable())
	{
		DoomIwadLogSelection("external", (unsigned int)eadk_external_data_size);
		return (unsigned int)eadk_external_data_size;
	}

	return 0;
}

#else

#ifndef EMBEDDED_IWAD_INCLUDE
YOU NEED TO SET THE WAD= TO A VALID WAD
#endif

#include EMBEDDED_IWAD_INCLUDE

const unsigned int doom_iwad_len = sizeof(doom_iwad);

const unsigned char* doom_iwad_data(void)
{
#if defined(NUMWORKS) && USE_EXTERNAL_IWAD
	if (NumWorksExternalIwadAvailable())
	{
		DoomIwadLogSelection("external", (unsigned int)eadk_external_data_size);
		return (const unsigned char*)eadk_external_data;
	}
#endif

	DoomIwadLogSelection("embedded", doom_iwad_len);
	return doom_iwad;
}

unsigned int doom_iwad_size(void)
{
#if defined(NUMWORKS) && USE_EXTERNAL_IWAD
	if (NumWorksExternalIwadAvailable())
	{
		DoomIwadLogSelection("external", (unsigned int)eadk_external_data_size);
		return (unsigned int)eadk_external_data_size;
	}
#endif

	DoomIwadLogSelection("embedded", doom_iwad_len);
	return doom_iwad_len;
}

#endif
